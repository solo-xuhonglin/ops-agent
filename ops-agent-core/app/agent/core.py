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
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.errors import NodeCancelledError

from app.agent.context import TaskContext
from app.agent.graph import build_graph, run_graph, _maybe_register_tracker
from app.tools.http_client import AdminHttpClient
from app.tools.registry import ToolRegistry
from app.transport import agent_pb2
from app.transport.grpc_client import GrpcClient

log = logging.getLogger("agent.core")

SYSTEM_PROMPT = (
    "# 角色与身份\n"
    "你是 ops-agent 平台的**智能运维助手**，服务于公司内部的运维与业务技术团队。"
    "负责诊断训练任务、推理服务(serving)、数据集与模型状态，并回答运维相关的自然语言问询。\n"
    "\n"
    "# 核心目标\n"
    "帮助运维人员**高效、安全**地完成：系统状态查询、故障诊断、处置建议（写操作须经人工审批）。"
    "所有结论必须基于工具返回的真实数据，**禁止编造任何信息**。\n"
    "\n"
    "# 工作流程与步骤\n"
    "收到任务后遵循以下步骤：\n"
    "1. **理解需求**：分析用户请求，明确其最终目的（查询？诊断？处置？）。\n"
    "2. **信息收集**：如有必要，调用只读工具查询真实状态（数据集/模型/训练/serving），"
    "信息不足时可多次调用不同工具。\n"
    "3. **制定计划**：基于真实数据判断是否需要处置（发起训练、部署、下线异常 serving、中止卡住的训练等）。\n"
    "4. **处置确认（关键）**：任何写操作（is_write=true）**必须**经人工审批，严禁直接执行——"
    "在最终回答 content 末尾以 ```json``` 代码块给出审批建议，然后结束本轮，等待审批。\n"
    "5. **报告结果**：以清晰、结构化的 Markdown 向用户报告结论。\n"
    "\n"
    "# 工具使用规范\n"
    "- 工具清单与参数 schema 由系统在下发的【工具契约】中给出（每轮注入，含每个工具的 ⚠ 写标记），"
    "工具名必须严格取自清单，args 必须符合对应参数 schema。\n"
    "- 需要查询时，输出工具调用 JSON：```json {\"tool\":\"<工具名>\",\"args\":{...}} ```"
    "（需要并行时可输出 {\"tools\":[{\"tool\":\"a\",\"args\":{...}},{...}]}）；"
    "信息齐备后直接输出最终回答（Markdown），不要在最终回答里再输出工具调用 JSON。\n"
    "- **严禁直接调用任何写工具（is_write=true，契约中标注 ⚠ 写工具）**。写操作必须走审批闭环：\n"
    "  在最终回答 content 末尾追加 ```json {\"suggestions\":[{\"action_type\":\"...\","
    "\"target_type\":\"...\",\"target_id\":N,\"params\":{...},\"reason\":\"...\","
    "\"priority\":\"HIGH|NORMAL|LOW\"}]} ``` 代码块。\n"
    "  **该代码块必须出现在最终回答 content（用户可见）**，不能只出现在 reasoning——reasoning 不入库、"
    "用户看不到，无法触发审批。任务结束后用户在前端看到审批卡，审批通过后系统会派发新任务"
    "（该任务 suggestion_id>0，说明写操作已获审批）给你执行。\n"
    "- **多步计划（重要）**：如果用户请求需要**多个写操作步骤**（例如\"训练并部署\"），"
    "在最终回答末尾输出规划块（仅摘要，**不要输出 steps 数组**）+ 为每个写操作各输出一条 suggestion：\n"
    "  1. 规划块：```json {\"plan\":{\"summary\":\"训练并部署\"}} ```\n"
    "  2. 每条写操作一条 suggestion（系统按顺序编号为步骤，上一步执行完成（含异步训练/部署完成）后下一步自动出现在审批列表）：\n"
    "  ```json {\"suggestions\":[{\"action_type\":\"training_create\",\"target_type\":\"dataset\","
    "\"target_id\":96,\"params\":{},\"reason\":\"...\",\"priority\":\"HIGH\"},"
    "{\"action_type\":\"serving_deploy\",\"target_type\":\"model_version\",\"target_id\":0,"
    "\"params\":{},\"reason\":\"部署训练产出模型\",\"priority\":\"HIGH\"}]} ```\n"
    "- **plan 自主权**：执行或轮询过程中，若发现条件变化、步骤失败或需求不成立，可**主动修改 plan**"
    "（增删步骤）或**废弃 plan**（置 CANCELLED 并说明理由）；任何 plan 变更系统会以\"计划更新\"消息向用户展示。\n"
    "- **唯一例外**：当前任务的 suggestion_id>0（已审批的写操作），此时才可调用对应的写工具执行。\n"
    "- **禁止重复调用**：禁止在没有新信息的情况下，为同一请求重复调用同一个工具。\n"
    "- 若用户未提供工具所需的必要参数（如数据集 ID、目标 ID），主动、一次性地询问所有缺失信息。\n"
    "\n"
    "# 边界与限制\n"
    "- **安全红线**：严禁执行任何可能删除数据或破坏系统安全的操作；写操作一律走审批闭环。\n"
    "- **权限边界**：无审批授权的写操作不可执行；若工具返回 403（未授权），不要重试，应给出审批建议。\n"
    "- **知识边界**：所有回答必须基于工具返回的真实数据或内置知识库，**禁止编造**；"
    "信息不足时明确说明并给出获取途径。\n"
    "\n"
    "# 输出格式与风格\n"
    "- **语气**：专业、客观、简洁，使用中文。\n"
    "- **格式**：状态查询结果使用 Markdown 表格呈现；诊断结论使用清晰的标题分段；"
    "需要处置时附带 suggestions JSON 代码块。\n"
    "- **必要字段**：报告操作结果时，必须包含「操作目标」「操作结果」「后续建议」。"
)

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_plan(content: str) -> Optional[dict]:
    """解析最终回答中的规划块（多步写操作的意图摘要，无 steps 数组）：
    ```json {"plan":{"summary":"训练并部署"}} ```
    返回 plan dict；无 plan 块返回 None。步骤由独立的 suggestions 表达（各带 action_type），
    系统按顺序落 step_no。兼容旧格式（plan 内带 steps 数组时仅取 summary，steps 忽略）。
    """
    m = _JSON_BLOCK_RE.search(content or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    plan = data.get("plan")
    if not isinstance(plan, dict):
        return None
    plan.setdefault("summary", "")
    plan.pop("steps", None)  # 步骤由独立 suggestion 表达（无 steps 字段）
    return plan


def _build_prompt(d: "agent_pb2.TaskDispatch") -> tuple[str, str]:
    """user prompt：按任务特征给出专门指引（chat 任务按 suggestion_id/query 判断）。"""
    if d.suggestion_id:
        # 已审批的写操作任务：直接调对应写工具，grantKey 已随 TaskDispatch 下发
        return (
            "（已审批的写操作——请执行）",
            (d.query or "") + "\n请按 query 描述执行该写操作，完成后回报结果。",
        )
    if d.query:
        return "", d.query
    if d.target_id:
        return "", f"请诊断目标状态：{d.target_type}={d.target_id}"
    return "", "请汇总当前系统状态"


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
                          msg: agent_pb2.ServerMessage, max_rounds: int = 10,
                          tracker: Any = None, store: Any = None) -> None:
    d = msg.task_dispatch
    ctx = TaskContext(task_id=d.task_id, task_token=d.task_token,
                      target_type=d.target_type, target_id=d.target_id,
                      conversation_id=d.conversation_id,
                      suggestion_id=d.suggestion_id, grant_key=d.grant_key)
    await client.send_event(ctx.task_id, "progress", f"received task [{d.task_id[:8]}]")

    # execute 任务（已审批写操作）直调写工具，不过决策图
    if d.task_type == "execute":
        await handle_execute(client, registry, llm, http, ctx, d, store, tracker)
        return

    hint, user_prompt = _build_prompt(d)
    messages: list = [
        SystemMessage(content=SYSTEM_PROMPT + hint),
        *_build_history(d),
        HumanMessage(content=user_prompt),
    ]

    # task 行由 worker 直写（对话轮=chat）
    if store is not None and store.enabled:
        try:
            await store.insert_task(d.task_id, "chat", d.conversation_id,
                                    query=d.query or "")
        except Exception as e:  # noqa: BLE001 - 落库失败不阻塞任务
            log.warning("task insert failed: %s", e)

    try:
        graph = build_graph(llm=llm, http=http, registry=registry, client=client,
                            tracker=tracker, store=store)
        final_messages = await run_graph(graph, ctx, messages, max_rounds=max_rounds)

        content = _extract_conclusion(final_messages)
        suggestions = _parse_suggestions(content)
        if not suggestions:
            # 兜底：deepseek-reasoner 常把 suggestions JSON 放进 reasoning 而不放 content（用户看不到）。
            # 从聚合推理链里再提取一次，保证审批卡能生成。
            suggestions = _parse_suggestions(_extract_reasoning(final_messages))
        # 多步计划：解析 plan（无 steps 数组，plan 仅摘要；步骤=多条 suggestion）
        plan = _parse_plan(content) or _parse_plan(_extract_reasoning(final_messages))
        # plan/suggestion 由 worker 直写库
        if store is not None and store.enabled and ctx.conversation_id:
            await _persist_outputs(store, ctx, plan, suggestions)
        conclusion = _JSON_BLOCK_RE.sub("", content).strip()  # 建议块从结论剥离
        await client.send_result(ctx.task_id, ok=True, conclusion=conclusion,
                                 suggestions=_to_proto_suggestions(suggestions),
                                 reasoning=_extract_reasoning(final_messages))
        if store is not None and store.enabled:
            try:
                await store.finish_task(ctx.task_id, "SUCCEEDED", conclusion,
                                        _extract_reasoning(final_messages))
            except Exception as e:  # noqa: BLE001
                log.warning("task finish persist failed: %s", e)
        log.info("task done: %s suggestions=%s reasoning_len=%d", ctx.task_id,
                 len(suggestions), len(_extract_reasoning(final_messages)))
    except (asyncio.CancelledError, NodeCancelledError):
        # admin 超时/手动取消：不回发 result（admin 已置 CANCELLED，避免覆盖状态）
        log.info("task cancelled by admin: %s", ctx.task_id)
        if store is not None and store.enabled:
            try:
                await store.cancel_task(ctx.task_id, "cancelled by admin")
            except Exception:  # noqa: BLE001
                pass
        raise
    except Exception as e:  # noqa: BLE001 - 单任务失败不拖垮 worker
        log.error("task failed: %s", e, exc_info=True)
        if store is not None and store.enabled:
            try:
                await store.finish_task(ctx.task_id, "FAILED", f"task failed: {e}")
            except Exception:  # noqa: BLE001
                pass
        await client.send_result(ctx.task_id, ok=False,
                                 conclusion=f"task failed: {e}", error=str(e))


async def _persist_outputs(store: Any, ctx: TaskContext,
                           plan: Optional[dict], suggestions: list[dict]) -> None:
    """plan（若有）与 suggestion 业务行直写库。

    - 多步：INSERT agent_plans + N 条 PENDING suggestion（step_no 1..N，plan_id 关联）
    - 单条：INSERT 1 条 PENDING suggestion（plan_id 为空）
    提示词契约（P5）：已存在的 plan 不再重建，仅产出新步骤；重复由前端按 PENDING 去重兜底。
    """
    plan_id = ""
    if plan:
        plan["conversation_id"] = ctx.conversation_id
        plan["status"] = "RUNNING"
        plan_id = plan.get("plan_id") or ""
        try:
            await store.upsert_plan(plan)
            plan_id = plan.get("plan_id") or plan_id
        except Exception as e:  # noqa: BLE001
            log.warning("plan persist failed: %s", e)
            return
    for idx, s in enumerate(suggestions):
        s.setdefault("suggestion_id", "")
        if plan_id:
            s["plan_id"] = plan_id
            s["step_no"] = idx + 1
        s["source_task_id"] = ctx.task_id
        s["conversation_id"] = ctx.conversation_id
        try:
            await store.insert_suggestion(s)
        except Exception as e:  # noqa: BLE001
            log.warning("suggestion persist failed: %s", e)


EXECUTE_SUMMARY_SYSTEM = (
    "# 角色\n"
    "你是 ops-agent 平台的智能运维助手。你刚刚执行了一个**已获人工审批**的写操作，"
    "下面附上该操作的**原始执行结果**（工具返回 JSON）。\n"
    "\n"
    "# 输出要求\n"
    "1. 明确操作是否成功（成功 / 失败 / 已提交进行中）；\n"
    "2. 给出关键信息（如创建对象的 ID、当前状态、耗时等）；\n"
    "3. 如需后续处理（异步轮询、重试、下一步建议），一并说明。\n"
    "4. 必须严格基于原始结果，禁止编造；用中文；Markdown 简洁呈现。"
)


async def handle_execute(client: GrpcClient, registry: ToolRegistry, llm: Any,
                         http: AdminHttpClient, ctx: TaskContext,
                         d: agent_pb2.TaskDispatch, store: Any,
                         tracker: Any = None) -> None:
    """execute 任务——直调写工具（不过决策图）→ 原始结果回喂 LLM 总结 → 回写 suggestion。

    原始结果经 tool_call/tool_result 事件展示（前端时间线），LLM 总结作为结论落对话。
    异步写操作（training_create/serving_deploy）成功后注册 Monitor 轮询，由 tracker 推进 Plan。
    """
    tool = registry.get(d.action_type)
    if tool is None:
        log.warning("execute unknown action tool: %s", d.action_type)
        conclusion = f"unknown action tool: {d.action_type}"
        if store is not None and store.enabled:
            await store.finish_task(ctx.task_id, "FAILED", conclusion)
        await client.send_result(ctx.task_id, ok=False, conclusion=conclusion, error=conclusion)
        return

    params: dict = {}
    if d.params:
        try:
            params = json.loads(d.params) if isinstance(d.params, str) else dict(d.params)
        except (json.JSONDecodeError, TypeError):
            log.warning("execute params invalid, ignored: %s", str(d.params)[:100])

    await client.send_event(ctx.task_id, "tool_call",
                            json.dumps({"name": tool.name, "args": params}, ensure_ascii=False))
    result = await http.call(tool, params, ctx)
    body = result.get("body") if isinstance(result, dict) else result
    summary = str(body)[:500] if body is not None else ""
    await client.send_event(ctx.task_id, "tool_result",
                            json.dumps({"name": tool.name, "summary": summary}, ensure_ascii=False))
    ok = isinstance(result, dict) and result.get("status") in (200, 201, 202)

    # 异步写操作成功后注册对象状态轮询（训练/部署完成后推进 Plan）
    await _maybe_register_tracker(tracker, store, ctx, tool, result)

    # 原始结果回喂 LLM 生成执行总结（失败则结构化兜底）
    conclusion = ""
    try:
        resp = await llm.ainvoke([
            SystemMessage(content=EXECUTE_SUMMARY_SYSTEM),
            HumanMessage(content=json.dumps(result, ensure_ascii=False)),
        ])
        conclusion = str(getattr(resp, "content", "")).strip()
    except Exception as e:  # noqa: BLE001 - LLM 总结失败不阻塞任务
        log.warning("execute summary llm failed: %s", e)
    if not conclusion:
        conclusion = ("执行成功" if ok else "执行失败") + (f"：{summary}" if summary else "")

    # 回写 suggestion（条件：APPROVED/EXECUTING → EXECUTED/FAILED）
    if store is not None and store.enabled and ctx.suggestion_id:
        try:
            await store.update_suggestion_result(ctx.suggestion_id,
                                                 "EXECUTED" if ok else "FAILED", conclusion)
        except Exception as e:  # noqa: BLE001
            log.warning("suggestion result update failed: %s", e)
    if store is not None and store.enabled:
        try:
            await store.finish_task(ctx.task_id, "SUCCEEDED" if ok else "FAILED", conclusion)
        except Exception as e:  # noqa: BLE001
            log.warning("execute task finish failed: %s", e)
    await client.send_result(ctx.task_id, ok=ok, conclusion=conclusion)
    log.info("execute done: %s ok=%s", ctx.task_id[:8], ok)


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
