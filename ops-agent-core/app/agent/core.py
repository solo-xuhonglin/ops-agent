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
from app.agent.graph import (
    build_graph,
    run_graph,
    _extract_conclusion,
    _format_plan_summary,
    _maybe_register_tracker,
    flush_pending_trackers,
)
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
    "5. **观察对象状态**：审批后的写操作由系统执行（异步接口会跟踪对象直至终态）；"
    "执行中可用 `wait_until` 主动确认对象状态，回复前先确认对象当前状态，**不要假设它已成功**。\n"
    "   - 提交异步写操作后**必须**用 `wait_until` 等待对象到达终态，**禁止**直接反复调同名只读查询"
    "（如连续 `training_get` 调查训练状态）；系统会拒绝重复只读调用并提示改用 `wait_until`。\n"
    "6. **检查任务列表**：用只读工具核对真实状态（`training_get`/`serving_get`/`training_list`/"
    "`serving_list`/`dataset_get` 等），确认步骤结果是成功、失败还是仍在进行中，禁止凭记忆或推断。\n"
    "7. **再决定后续步骤**：基于观察结果推进计划——步骤成功先用 `plan_update` 把该步骤标记为 done，"
    "再继续下一步（`approve_<写操作名>` 带 plan_id/step_no）；步骤失败标记为 failed 后，"
    "决定重试（retry_of 原建议）或调整方案；全部步骤完成用 `plan_update` 将计划置 DONE。\n"
    "8. **报告结果**：以清晰、结构化的 Markdown 向用户报告结论（含操作目标/结果/后续建议）；"
    "任务收尾的**最终总结必须是一段 Markdown 消息**（用 `##` 标题分段、列表或表格呈现），"
    "便于前端直接渲染，**不要用一整段纯文本**作为结尾。\n"
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
    "- **异步等待用 wait_until**：提交异步写操作（training_create/serving_deploy）后需要确认结果、"
    "或对象可能仍在进行中需要等待状态变化时，调用 `wait_until(query_tool, object_id, "
    "wait_seconds=60~120, target_status)` —— 系统代为轮询：状态变化 / updated_at 更新 / "
    "到达 target_status / 进入终态会立即返回最新状态，超时则返回当前状态（仍在进行中）。\n"
    "- **不要滥用 wait_until**：普通状态查询直接用 training_get/serving_get 等只读工具；"
    "已确认到达终态不等待；非异步操作不等待。wait_until 占用任务轮次，连续等待不要超过数分钟；"
    "超时返回仍在进行中且预算将尽时，汇报「仍在进行中，系统会在完成时继续处理」，不要无限等待。\n"
    "- **sleep 用于纯等待**：当你需要给后端操作留时间（限流、缓存写入、冷却）而不关心对象状态变化时，"
    "用 `sleep(seconds)` 在当前任务内纯等待 N 秒（单次 1-300 秒）；**不要**用 sleep 替代 wait_until"
    "——等待异步对象状态变化仍应使用 wait_until。\n"
            "- **禁止重复调用**：禁止在没有新信息的情况下，为同一请求重复调用同一个工具。\n"
            "- **提交审批后停止**：调用 `approve_<写操作名>` 提交处置建议后，不要再调用任何工具；"
            "系统会暂停等待人工审批，审批通过后会由独立的执行任务继续推进计划。"
            "提交后只需简短汇报建议已提交（可含 suggestion_id）即可，无需轮询或继续推理。\n"
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
    "- **必要字段**：报告操作结果时，必须包含「操作目标」「操作结果」「后续建议」。\n"
    "- **最终总结**：任务收尾输出给用户的最终总结必须是 **Markdown 格式消息**（用 `##` 标题、"
    "列表或表格呈现，如「## 总结」「- 操作目标：…」），前端会按 Markdown 渲染；"
    "不要以一整段纯文本收尾。\n"
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
    """取最后一条 assistant 消息的推理链（落库兜底单行）。

    graph agent_node 已在每轮 AIMessage.additional_kwargs['reasoning_content'] 挂入该轮
    完整推理。多轮推理已由 admin 在 SSE 流期间按 LLM 轮次实时落库为独立的 ASSISTANT
    消息行（与 TOOL_CALL 行交错），刷新/重进后时间顺序与运行中完全一致；此处仅作为
    无流内落库路径（如无 worker 的失败兜底）的兜底单行推理。
    """
    for m in reversed(messages):
        if getattr(m, "type", "") == "ai":
            kw = getattr(m, "additional_kwargs", None) or {}
            return kw.get("reasoning_content") or ""
    return ""


async def handle_dispatch(client: GrpcClient, registry: ToolRegistry,
                          llm: Any, http: AdminHttpClient,
                          msg: agent_pb2.ServerMessage, max_rounds: int = 10,
                          tracker: Any = None, store: Any = None,
                          msg_store: Any = None) -> None:
    d = msg.task_dispatch
    ctx = TaskContext(task_id=d.task_id, task_token=d.task_token,
                      target_type=d.target_type, target_id=d.target_id,
                      conversation_id=d.conversation_id,
                      suggestion_id=d.suggestion_id, grant_key=d.grant_key,
                      reasoning_enabled=bool(d.reasoning_enabled))
    await client.send_event(ctx.task_id, "progress", f"received task [{d.task_id[:8]}]")

    # execute 任务（已审批写操作）：系统直调写工具后任务内决策图闭环推进
    if d.task_type == "execute":
        await handle_execute(client, registry, llm, http, ctx, d, store, tracker,
                             max_rounds=max_rounds, msg_store=msg_store)
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
                            tracker=tracker, store=store, msg_store=msg_store)
        final_messages, hit_limit = await run_graph(graph, ctx, messages, max_rounds=max_rounds)

        content = _extract_conclusion(final_messages)
        # 写操作建议由 approve_<写操作名> / plan_create 工具落库，收敛后不再解析 JSON 建议块
        conclusion = content.strip()
        if hit_limit:
            conclusion = (f"⚠️ 任务因工具调用轮次达到上限（{max_rounds} 轮）而自动停止。"
                          f"\n\n{conclusion}\n\n"
                          f"建议：精简任务再继续对话，或明确拆分多步计划（plan_create）后逐步推进。")
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


EXECUTE_LOOP_SYSTEM = (
    "# 角色\n"
    "你刚执行了一个**已获人工审批**的写操作（结果由系统注入，你不需要、也不允许再次执行该写操作）。\n"
    "\n"
    "# 任务\n"
    "1. 判断操作是否成功（成功 / 失败 / 已提交进行中），给出关键信息（对象 ID、当前状态等）；\n"
    "2. 若为异步操作（训练/部署）：用 wait_until 轮询对象状态直至到达终态或发生变化"
    "（单次 60~120 秒，可连续调用；超时返回仍在进行中且预算将尽时，汇报现状并结束，系统会继续跟踪）；\n"
    "3. 若关联 plan：按 plan 推进——步骤成功先用 plan_update 标记 done，再提下一步 approve_<写操作名>"
    "（带 plan_id/step_no）；步骤失败标记 failed 并决定重试（approve_* 带 retry_of）或调整/废弃；"
    "全部完成用 plan_update 置 plan DONE/FAILED；\n"
    "3b. 提出下一步 approve_<写操作名> 后本任务即结束，等待人工审批，无需继续轮询；"
    "同一计划的多步处置应逐步提交、逐步等待审批。\n"
    "4. 写操作失败：分析失败原因——参数可修正则 approve_<写操作名> 提出修正建议"
    "（retry_of 关联原建议），方案不可行则说明放弃（必要时 plan_update 调整计划）；\n"
    "5. 非工具调用时收敛：用中文 Markdown 报告操作结果（含「操作目标 / 操作结果 / 后续建议」）。\n"
    "所有判断必须基于工具返回的真实数据，禁止编造。"
)


async def handle_execute(client: GrpcClient, registry: ToolRegistry, llm: Any,
                         http: AdminHttpClient, ctx: TaskContext,
                         d: agent_pb2.TaskDispatch, store: Any,
                         tracker: Any = None, max_rounds: int = 10,
                         msg_store: Any = None) -> None:
    """execute 任务——系统直调写工具（安全边界）→ 同一任务内决策图自主推进 → 收敛。

    写工具结果以观察注入任务内决策图：agent 用 wait_until 轮询异步对象、plan_update 推进
    plan、approve_* 提下一步建议（失败时带 retry_of 修正重试）；非工具调用即收敛。
    写工具本体绝不在 tools 列表（模型无直接执行路径）；suggestion 状态/task 落库/
    Monitor 注册等收尾行为保留。
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

    # 落 RUNNING 任务行（admin 端 TaskResult 反查 suggestionId 需要；与 chat 一致）
    if store is not None and store.enabled:
        try:
            await store.insert_task(ctx.task_id, "execute", ctx.conversation_id,
                                    query=d.params or "", suggestion_id=ctx.suggestion_id)
        except Exception as e:  # noqa: BLE001
            log.warning("execute task insert failed: %s", e)

    # 异步写操作成功后注册对象状态轮询（任务内 wait_until 预算用尽后由 Monitor 兜底）
    await _maybe_register_tracker(tracker, store, ctx, tool, result)

    # 决策图内闭环：写工具结果注入 → agent 自主推进（wait_until/plan_update/approve_*）
    # → 非工具调用收敛。失败同样在图内（agent 看失败原因决定重试/放弃）。
    conclusion = ""
    reasoning_text = ""
    try:
        plan_text = ""
        if store is not None and store.enabled and ctx.suggestion_id:
            sug = await store.get_suggestion(ctx.suggestion_id)
            if sug and sug.get("plan_id"):
                plan_text = _format_plan_summary(await store.get_plan(sug["plan_id"]))
        observation = json.dumps({
            "operation": tool.name,
            "write_result": result,
            "plan": plan_text or "（无关联计划）",
        }, ensure_ascii=False)
        messages = [
            SystemMessage(content=SYSTEM_PROMPT + "\n\n" + EXECUTE_LOOP_SYSTEM),
            HumanMessage(content=observation),
        ]
        graph = build_graph(llm_runtime=llm, http=http, registry=registry, client=client,
                            tracker=tracker, store=store, msg_store=msg_store)
        final_messages, hit_limit = await run_graph(graph, ctx, messages, max_rounds=max_rounds)
        conclusion = _extract_conclusion(final_messages).strip()
        reasoning_text = _extract_reasoning(final_messages)
        if hit_limit:
            conclusion = (f"⚠️ 任务因工具调用轮次达到上限（{max_rounds} 轮）而自动停止。"
                          f"\n\n{conclusion}\n\n"
                          f"建议：精简任务再继续对话，或用 plan_create 拆分多步计划。")
    except Exception as e:  # noqa: BLE001 - 图内闭环失败不阻塞 execute 收尾
        log.warning("execute graph loop failed: %s", e)
    if not conclusion:
        conclusion = ("执行成功" if ok else "执行失败") + (f"：{summary}" if summary else "")

    # 回写 suggestion（条件：APPROVED/EXECUTING → EXECUTED/FAILED）
    if store is not None and store.enabled and ctx.suggestion_id:
        try:
            await store.update_suggestion_result(ctx.suggestion_id,
                                                 "EXECUTED" if ok else "FAILED", conclusion)
        except Exception as e:  # noqa: BLE001
            log.warning("suggestion result update failed: %s", e)
    # 收敛时统一注册 Monitor：模型自己 wait_until 等到终态的对象跳过（避免双轨重复推进），
    # 仍在进行中的对象兜底注册（任务结束由 Monitor 后台推进 plan）
    try:
        await flush_pending_trackers(tracker, registry, http, ctx)
    except Exception as e:  # noqa: BLE001 - flush 失败不阻塞 execute 收尾
        log.warning("flush pending trackers failed: %s", e)
    if store is not None and store.enabled:
        try:
            await store.finish_task(ctx.task_id, "SUCCEEDED" if ok else "FAILED", conclusion,
                                    reasoning_text)
        except Exception as e:  # noqa: BLE001
            log.warning("execute task finish failed: %s", e)
    # 审批后续推理必须回传 reasoning，否则 finishAssistant 落库的 assistant 消息
    # reasoning 为空，会话恢复/前端刷新时该轮推理记录缺失（chat 任务已回传，execute 漏传）。
    await client.send_result(ctx.task_id, ok=ok, conclusion=conclusion,
                             reasoning=reasoning_text)
    log.info("execute done: %s ok=%s reasoning_len=%d",
             ctx.task_id[:8], ok, len(reasoning_text))



