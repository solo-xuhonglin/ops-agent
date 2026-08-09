"""Agent 决策入口（M3.5+，核心为 LangGraph + 标准 LangChain 生态）。

收到 TaskDispatch → 组装初始消息（SystemMessage + 可选多轮 history + HumanMessage）→
交给 graph.run_graph 执行决策图（agent 决策节点 ↔ tools 执行节点循环，LLM 自主决定调用
哪些工具；agent 节点流式产出，增量以 thinking/delta/tool_call/tool_result 事件实时回传）→
收敛后解析结论 + suggestions(JSON 代码块) → TaskResult（含聚合推理链全文）。
对外契约（TaskEvent → TaskResult）不变；写操作经"建议→人工确认→grantKey→execute 任务"闭环。
"""
import asyncio
import json
import logging
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.errors import NodeCancelledError

from app.agent.context import TaskContext
from app.agent.graph import build_graph, run_graph
from app.tools.http_client import AdminHttpClient
from app.tools.registry import ToolRegistry
from app.transport import agent_pb2
from app.transport.grpc_client import GrpcClient

log = logging.getLogger("agent.core")

SYSTEM_PROMPT = (
    "你是 ops-agent 的运维助手，负责诊断训练任务、推理服务(serving)、数据集与模型状态，"
    "并回答运维相关的自然语言问询。\n"
    "【严格授权规则——写操作必须走人工审批】\n"
    "  严禁在常规问询/诊断任务中直接调用任何写工具（is_write=true，例如 training_create、"
    "training_delete、serving_deploy、serving_undeploy、dataset_create、dataset_update、"
    "dataset_collect、dataset_delete）。所有写操作必须经人工审批闭环：\n"
    "    1) 不要调用写工具（即使你能）；\n"
    "    2) 在最终回答末尾追加 ```json {\"suggestions\":[{\"action_type\":\"...\","
    "\"target_type\":\"...\",\"target_id\":N,\"params\":{...},\"reason\":\"...\","
    "\"priority\":\"HIGH|NORMAL|LOW\"}]} ``` 代码块说明需要的写操作；\n"
    "    3) 任务结束 → 用户在前端看到审批卡 → 审批后系统会派发新任务（taskType="
    "execute_suggestion，任务描述含 suggestionId）给你执行。\n"
    "  唯一例外：当前任务的 taskType=execute_suggestion 且任务描述含 suggestionId，"
    "说明该写操作已获人工审批，此时才可调用对应的写工具执行。\n"
    "你可以调用只读工具（is_write=false）查询系统真实状态；"
    "基于工具返回的数据给出简洁、准确的中文结论；信息不足时可多次调用不同工具。\n"
    "工具调用遵循系统下发的【输出契约】：需要查询时输出 {\"tool\":...,\"args\":...} JSON 块，"
    "信息齐备后直接输出最终回答（markdown），不要在最终回答里再输出工具调用 JSON。"
)

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _build_prompt(d: "agent_pb2.TaskDispatch") -> tuple[str, str]:
    """user prompt：按任务类型给出专门指引。"""
    if d.task_type == "execute_suggestion":
        # 已审批任务：直接调对应写工具，grantKey 已就位（admin 端 aspect 校验）。
        return (
            "（任务类型：execute_suggestion——已审批的写操作，请执行）",
            (d.query or "") + "\n请按 query 描述执行该写操作，完成后回报结果。",
        )
    if d.task_type == "training_completed_followup":
        # 训练完成自动触发的部署评估：query 已包含 modelVersionId/metrics。
        return (
            "（任务类型：training_completed_followup——训练已完成，自动评估是否部署）",
            (d.query or "") + "\n如需部署，推送 action_type=serving_deploy、target_type=model_version、"
            "target_id 为 modelVersionId 的 suggestions JSON 块。",
        )
    hint = ""
    if d.task_type and d.task_type != "question":
        hint = f"（任务类型：{d.task_type}）"
    if d.query:
        return hint, d.query
    if d.target_id:
        return hint, f"请诊断目标状态：{d.target_type}={d.target_id}"
    return hint, "请汇总当前系统状态"


def _build_history(d: "agent_pb2.TaskDispatch") -> list:
    """多轮对话历史：TaskDispatch.history 为 JSON 数组 [{"role":"user|assistant","content":...}]。

    解析失败或角色未知的条目直接丢弃；history 只用于给 LLM 提供上文，不做校验。
    """
    if not d.history:
        return []
    try:
        raw = json.loads(d.history)
    except (json.JSONDecodeError, TypeError):
        log.warning("invalid task history, ignored: %s", str(d.history)[:200])
        return []
    if not isinstance(raw, list):
        return []
    messages: list = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content") or ""
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages


def _extract_reasoning(messages: list) -> str:
    """取最后一条 assistant 消息的聚合推理链全文（graph agent_node 挂到 additional_kwargs）。"""
    for m in reversed(messages):
        if getattr(m, "type", "") == "ai":
            kw = getattr(m, "additional_kwargs", None) or {}
            return kw.get("reasoning_content") or ""
    return ""


async def handle_dispatch(client: GrpcClient, registry: ToolRegistry,
                          llm: Any, http: AdminHttpClient,
                          msg: agent_pb2.ServerMessage, max_rounds: int = 10) -> None:
    d = msg.task_dispatch
    ctx = TaskContext(task_id=d.task_id, task_token=d.task_token,
                      target_type=d.target_type, target_id=d.target_id)
    await client.send_event(ctx.task_id, "progress", f"received task [{d.task_type}]")

    hint, user_prompt = _build_prompt(d)
    messages: list = [
        SystemMessage(content=SYSTEM_PROMPT + hint),
        *_build_history(d),
        HumanMessage(content=user_prompt),
    ]

    try:
        graph = build_graph(llm=llm, http=http, registry=registry, client=client)
        final_messages = await run_graph(graph, ctx, messages, max_rounds=max_rounds)

        content = _extract_conclusion(final_messages)
        suggestions = _parse_suggestions(content)
        conclusion = _JSON_BLOCK_RE.sub("", content).strip()  # 建议块从结论剥离
        await client.send_result(ctx.task_id, ok=True, conclusion=conclusion,
                                 suggestions=_to_proto_suggestions(suggestions),
                                 reasoning=_extract_reasoning(final_messages))
        log.info("task done: %s suggestions=%s reasoning_len=%d", ctx.task_id,
                 len(suggestions), len(_extract_reasoning(final_messages)))
    except (asyncio.CancelledError, NodeCancelledError):
        # admin 超时/手动取消：不回发 result（admin 已置 CANCELLED，避免覆盖状态）
        log.info("task cancelled by admin: %s", ctx.task_id)
        raise
    except Exception as e:  # noqa: BLE001 - 单任务失败不拖垮 worker
        log.error("task failed: %s", e, exc_info=True)
        await client.send_result(ctx.task_id, ok=False,
                                 conclusion=f"task failed: {e}", error=str(e))


def _extract_conclusion(messages: list) -> str:
    """取最后一条 assistant 消息（有内容且非工具调用轮才用），否则提示未收敛。"""
    for m in reversed(messages):
        if getattr(m, "type", "") == "ai" and getattr(m, "content", None):
            kw = getattr(m, "additional_kwargs", None) or {}
            if kw.get("_is_tool_round"):
                continue  # 工具调用轮的内容是 JSON，不是结论
            return m.content
    return "no conclusion produced (max tool rounds reached)"


def _parse_suggestions(content: str) -> list[dict]:
    """从 LLM 回答的 ```json 代码块解析 suggestions 列表（缺 action_type/target_id 的丢弃）。"""
    m = _JSON_BLOCK_RE.search(content or "")
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    raw = data.get("suggestions") or []
    return [s for s in raw
            if isinstance(s, dict) and s.get("action_type") and s.get("target_id")]


def _to_proto_suggestions(items: list[dict]) -> list[agent_pb2.Suggestion]:
    out = []
    for s in items:
        out.append(agent_pb2.Suggestion(
            action_type=str(s.get("action_type", "")),
            target_type=str(s.get("target_type", "")),
            target_id=int(s.get("target_id", 0)),
            params=json.dumps(s.get("params", {}), ensure_ascii=False),
            reason=str(s.get("reason", "")),
            priority=str(s.get("priority", "NORMAL")),
        ))
    return out
