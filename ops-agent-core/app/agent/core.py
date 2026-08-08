"""Agent 决策入口（M3.5，核心已替换为 LangGraph）。

收到 TaskDispatch → 组装初始消息 → 交给 graph.run_graph 执行决策图
（agent 决策节点 ↔ tools 执行节点循环，LLM 自主决定调用哪些工具）→
收敛后解析结论 + suggestions(JSON 代码块) → TaskResult。
对外契约（TaskEvent → TaskResult）不变；写操作经"建议→人工确认→grantKey→execute 任务"闭环。
"""
import json
import logging
import re

from app.agent.context import TaskContext
from app.agent.graph import build_graph, run_graph
from app.llm.deepseek import DeepSeekClient
from app.tools.http_client import AdminHttpClient
from app.tools.registry import ToolRegistry
from app.transport import agent_pb2
from app.transport.grpc_client import GrpcClient

log = logging.getLogger("agent.core")

SYSTEM_PROMPT = (
    "你是 ops-agent 的运维助手，负责诊断训练任务、推理服务(serving)、数据集与模型状态，"
    "并回答运维相关的自然语言问询。你可以调用工具查询系统真实状态；"
    "基于工具返回的数据给出简洁、准确的中文结论；信息不足时可多次调用不同工具。"
    "若任务明确要求执行已获授权的处置操作（如下线/中止/部署，任务描述中带 suggestionId），"
    "直接调用对应的写工具执行并汇报结果。"
    "若诊断发现需要处置（下线异常 serving、中止卡住的训练、部署模型等），在最终回答末尾附加"
    " JSON 代码块给出处置建议：```json {\"suggestions\":[{\"action_type\":\"serving_undeploy\","
    "\"target_type\":\"serving_endpoint\",\"target_id\":3,\"reason\":\"原因说明\","
    "\"priority\":\"HIGH\"}]} ```（写操作会经人工确认后由你执行，无需在当前回答中直接执行）。"
)

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _build_prompt(d: "agent_pb2.TaskDispatch") -> tuple[str, str]:
    """user prompt：query 优先，否则用 target 构造诊断指令（task_type 仅作轻提示）。"""
    hint = ""
    if d.task_type and d.task_type != "question":
        hint = f"（任务类型：{d.task_type}）"
    if d.query:
        return hint, d.query
    if d.target_id:
        return hint, f"请诊断目标状态：{d.target_type}={d.target_id}"
    return hint, "请汇总当前系统状态"


async def handle_dispatch(client: GrpcClient, registry: ToolRegistry,
                          llm: DeepSeekClient, http: AdminHttpClient,
                          msg: agent_pb2.ServerMessage, max_rounds: int = 10) -> None:
    d = msg.task_dispatch
    ctx = TaskContext(task_id=d.task_id, task_token=d.task_token,
                      target_type=d.target_type, target_id=d.target_id)
    await client.send_event(ctx.task_id, "progress", f"received task [{d.task_type}]")

    hint, user_prompt = _build_prompt(d)
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT + hint},
        {"role": "user", "content": user_prompt},
    ]

    try:
        graph = build_graph(llm=llm, http=http, registry=registry, client=client)
        final_messages = await run_graph(graph, ctx, messages, max_rounds=max_rounds)

        content = _extract_conclusion(final_messages)
        suggestions = _parse_suggestions(content)
        conclusion = _JSON_BLOCK_RE.sub("", content).strip()  # 建议块从结论剥离
        await client.send_result(ctx.task_id, ok=True, conclusion=conclusion,
                                 suggestions=_to_proto_suggestions(suggestions))
        log.info("task done: %s suggestions=%s", ctx.task_id, len(suggestions))
    except Exception as e:  # noqa: BLE001 - 单任务失败不拖垮 worker
        log.error("task failed: %s", e, exc_info=True)
        await client.send_result(ctx.task_id, ok=False,
                                 conclusion=f"task failed: {e}", error=str(e))


def _extract_conclusion(messages: list[dict]) -> str:
    """取最后一条 assistant content（有内容才用），否则提示未收敛。"""
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("content"):
            return m["content"]
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
