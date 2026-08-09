"""Agent 决策入口（核心为 LangGraph + 标准 LangChain 生态）。

收到 TaskDispatch → 组装初始消息（SystemMessage + 可选多轮 history + HumanMessage）→
交给 graph.run_graph 执行决策图（agent 决策节点 ↔ tools 执行节点循环，LLM 自主决定调用
哪些工具；agent 节点流式产出，增量以 thinking/delta/tool_call/tool_result 事件实时回传）→
收敛后解析结论 → TaskResult（含聚合推理链全文）。
对外契约（TaskEvent → TaskResult）不变；写操作经"审批工具（approve_<写操作名>/plan_create）→
人工确认 → grantKey → execute 任务"闭环；execute 任务直调写工具并回喂 LLM 总结。
"""
import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.errors import NodeCancelledError

from app.agent.context import TaskContext
from app.agent.decision import run_decision_round
from app.agent.graph import build_graph, run_graph, _maybe_register_tracker
from app.agent.tracker import Monitor
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
    "信息不足时可多次调用不同工具（支持并行调用独立工具）。\n"
    "3. **制定计划**：基于真实数据判断是否需要处置（发起训练、部署、下线异常 serving、中止卡住的训练等）；"
    "多步骤任务先调用 `plan_create` 记录执行计划（步骤清单与顺序）。\n"
    "4. **处置确认（关键）**：需要写操作时调用对应的 `approve_<写操作名>` 工具（如 "
    "`approve_training_create`）提出审批建议，严禁直接执行写操作。审批通过后由系统自动执行写操作，"
    "并在后台持续观察对象状态。\n"
    "5. **观察对象状态**：审批后的写操作是**异步执行**的——系统执行后会在后台跟踪对象"
    "（训练任务/serving 端点等）直至终态，对话中可能出现\"执行中/计划推进\"通知。"
    "回复前先确认对象当前状态，**不要假设它已成功**。\n"
    "6. **检查任务列表**：用只读工具核对真实状态（`training_get`/`serving_get`/`training_list`/"
    "`serving_list`/`dataset_get` 等），确认步骤结果是成功、失败还是仍在进行中，禁止凭记忆或推断。\n"
    "7. **再决定后续步骤**：基于观察结果推进计划——步骤成功先用 `plan_update` 把该步骤标记为 done，"
    "再继续下一步（`approve_<写操作名>` 带 plan_id/step_no）；步骤失败标记为 failed 后，"
    "决定重试（retry_of 原建议）或调整方案；全部步骤完成用 `plan_update` 将计划置 DONE。\n"
    "8. **报告结果**：以清晰、结构化的 Markdown 向用户报告结论（含操作目标/结果/后续建议）。\n"
    "\n"
    "# 工具使用规范\n"
    "- 工具列表由系统通过 function calling 下发（只读工具 + plan_create/plan_update + "
    "approve_<写操作名> 审批工具）；按需调用，参数必须符合对应 schema。\n"
    "- **复杂任务先规划**：任务包含**多个步骤**（例如\"训练并部署\"）时，先调用 `plan_create` "
    "记录执行计划（步骤清单与顺序）。\n"
    "- **写操作审批**：需要写操作时，调用对应的 `approve_<写操作名>` 工具（如发起训练 → "
    "`approve_training_create`）提出处置建议（系统生成待审批建议，审批通过后自动执行）。"
    "**普通查询/纯回答不需要调用**。\n"
    "- **你没有直接执行写操作的能力**：系统中不存在可直接调用的写工具，一切写操作必须经审批。\n"
    "- **plan 失败可重新规划**：若计划中某步骤执行失败导致计划无法继续（对话中会出现\"计划失败\"通知），"
    "重新调用 `plan_create` 制定新的执行方案即可。\n"
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
    "需要处置时调用 approve_<写操作名> 工具产出审批建议。\n"
    "- **必要字段**：报告操作结果时，必须包含「操作目标」「操作结果」「后续建议」。"
)


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
                      suggestion_id=d.suggestion_id, grant_key=d.grant_key,
                      reasoning_enabled=bool(d.reasoning_enabled))
    await client.send_event(ctx.task_id, "progress", f"received task [{d.task_id[:8]}]")

    # execute 任务（已审批写操作）直调写工具，不过决策图
    if d.task_type == "execute":
        await handle_execute(client, registry, llm, http, ctx, d, store, tracker)
        return

    # continue 任务（execute 成功后 admin 自动派发推进 plan 步骤）：复用决策轮
    if d.task_type == "continue":
        await handle_continue(client, registry, llm, http, ctx, d, store, tracker)
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
        graph = build_graph(llm_runtime=llm, http=http, registry=registry, client=client,
                            tracker=tracker, store=store)
        final_messages = await run_graph(graph, ctx, messages, max_rounds=max_rounds)

        content = _extract_conclusion(final_messages)
        # 写操作建议由 approve_<写操作名> / plan_create 工具落库，收敛后不再解析 JSON 建议块
        conclusion = content.strip()
        await client.send_result(ctx.task_id, ok=True, conclusion=conclusion,
                                 reasoning=_extract_reasoning(final_messages))
        if store is not None and store.enabled:
            try:
                await store.finish_task(ctx.task_id, "SUCCEEDED", conclusion,
                                        _extract_reasoning(final_messages))
            except Exception as e:  # noqa: BLE001
                log.warning("task finish persist failed: %s", e)
        log.info("task done: %s reasoning_len=%d", ctx.task_id,
                 len(_extract_reasoning(final_messages)))
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

    # execute_suggestion 单步执行：写工具本体（仅一次），带 call_id 便于 admin 端配对 call/result
    call_id = f"exec_{uuid.uuid4().hex[:8]}"
    await client.send_event(ctx.task_id, "tool_call",
                            json.dumps({"id": call_id, "name": tool.name, "args": params},
                                       ensure_ascii=False))
    result = await http.call(tool, params, ctx)
    body = result.get("body") if isinstance(result, dict) else result
    summary = str(body)[:500] if body is not None else ""
    await client.send_event(ctx.task_id, "tool_result",
                            json.dumps({"id": call_id, "name": tool.name, "summary": summary},
                                       ensure_ascii=False))
    ok = isinstance(result, dict) and result.get("status") in (200, 201, 202)

    # 落 RUNNING 任务行（admin 端 TaskResult 反查 suggestionId 需要；与 chat/continue 一致）
    if store is not None and store.enabled:
        try:
            await store.insert_task(ctx.task_id, "execute", ctx.conversation_id,
                                    query=d.params or "", suggestion_id=ctx.suggestion_id)
        except Exception as e:  # noqa: BLE001
            log.warning("execute task insert failed: %s", e)

    # 异步写操作成功后注册对象状态轮询（训练/部署完成后推进 Plan）
    await _maybe_register_tracker(tracker, store, ctx, tool, result)

    # 原始结果回喂 LLM 生成执行总结（失败则结构化兜底）；execute 用 fast 模式（无需思考，省 token）
    conclusion = ""
    try:
        resp = await llm.select(False).ainvoke([
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

    # 业务失败（非 HTTP 断连）：触发失败决策轮 —— 模型看失败原因决定修正重试（retry_of 新建议）
    # 或放弃；决策文本并入结论回发（新 PENDING 建议前端可见，人工再审批后重试）
    if not ok and ctx.suggestion_id and store is not None and store.enabled:
        try:
            from app.agent.decision import run_failure_decision
            original = await store.get_suggestion(ctx.suggestion_id)
            decision = await run_failure_decision(llm, http, registry, client, store,
                                                  tracker, ctx,
                                                  failure_text=summary or str(result),
                                                  original=original)
            if decision and decision != "（决策完成，无说明）":
                conclusion = f"{conclusion}\n\n**失败处置建议**：{decision}"
        except Exception as e:  # noqa: BLE001 - 失败决策不阻塞 execute 收尾
            log.warning("failure decision skipped: %s", e)
    if store is not None and store.enabled:
        try:
            await store.finish_task(ctx.task_id, "SUCCEEDED" if ok else "FAILED", conclusion)
        except Exception as e:  # noqa: BLE001
            log.warning("execute task finish failed: %s", e)
    await client.send_result(ctx.task_id, ok=ok, conclusion=conclusion)
    log.info("execute done: %s ok=%s", ctx.task_id[:8], ok)


async def handle_continue(client: GrpcClient, registry: ToolRegistry, llm: Any,
                          http: AdminHttpClient, ctx: TaskContext,
                          d: agent_pb2.TaskDispatch, store: Any,
                          tracker: Any = None) -> None:
    """continue 任务：admin 在 execute 成功且 suggestion 关联 plan 时自动派发，
    让模型复用决策轮推进 plan（提下一步 approve_<写操作> 或 plan_update 收尾）。

    observation：d.query 整段作为执行观察（admin 派发时把执行总结 + 上下文编码进 query）。
    无 plan_id 的建议直接跳过（单步任务无需推进）。
    """
    if store is None or not store.enabled:
        log.warning("continue unavailable: agent DB disabled")
        await client.send_result(ctx.task_id, ok=False, conclusion="agent DB disabled")
        return
    try:
        suggestion = await store.get_suggestion(ctx.suggestion_id)
    except Exception as e:  # noqa: BLE001
        log.warning("continue: load suggestion failed: %s", e)
        await client.send_result(ctx.task_id, ok=False, conclusion=f"load suggestion failed: {e}")
        return
    if not suggestion or not suggestion.get("plan_id"):
        log.info("continue skipped: no plan_id on suggestion=%s", ctx.suggestion_id)
        await client.send_result(ctx.task_id, ok=True, conclusion="（该建议无关联计划，无需推进）")
        return
    try:
        await store.insert_task(d.task_id, "continue", d.conversation_id,
                                query=d.query or "", suggestion_id=ctx.suggestion_id)
    except Exception as e:  # noqa: BLE001
        log.warning("continue task insert failed: %s", e)

    monitor = Monitor(
        object_type=suggestion.get("target_type", ""),
        object_id=suggestion.get("target_id", 0),
        conversation_id=ctx.conversation_id,
        task_id=ctx.task_id,
        task_token=ctx.task_token,
        query_tool="", query_args={},
        plan_id=suggestion["plan_id"],
        suggestion_id=ctx.suggestion_id,
        action_type=suggestion.get("action_type", ""),
    )
    observation = d.query or "（无）"
    try:
        decision_text = await run_decision_round(
            llm, http, registry, client, store, tracker,
            monitor, terminal_status="SUCCEEDED", observation=observation)
        conclusion = decision_text or "（决策完成，无说明）"
        await client.send_result(ctx.task_id, ok=True, conclusion=conclusion)
        try:
            await store.finish_task(ctx.task_id, "SUCCEEDED", conclusion)
        except Exception as e:  # noqa: BLE001
            log.warning("continue finish persist failed: %s", e)
        log.info("continue done: plan=%s step=%s -> %s",
                 monitor.plan_id, suggestion.get("step_no"), conclusion[:60])
    except (asyncio.CancelledError, NodeCancelledError):
        log.info("continue cancelled: %s", ctx.task_id)
        try:
            await store.cancel_task(ctx.task_id, "cancelled by admin")
        except Exception:  # noqa: BLE001
            pass
        raise
    except Exception as e:  # noqa: BLE001 - 单任务失败不拖垮 worker
        log.error("continue failed: %s", e, exc_info=True)
        try:
            await store.finish_task(ctx.task_id, "FAILED", str(e))
        except Exception:  # noqa: BLE001
            pass
        await client.send_result(ctx.task_id, ok=False, conclusion=f"continue failed: {e}")


def _extract_conclusion(messages: list) -> str:
    """取最后一条 assistant 消息（有内容且非工具调用轮才用），否则提示未收敛。"""
    for m in reversed(messages):
        if getattr(m, "type", "") == "ai" and getattr(m, "content", None):
            if getattr(m, "tool_calls", None):
                continue  # 工具调用轮（原生 tool_calls），内容非结论
            return m.content
    return "no conclusion produced (max tool rounds reached)"
