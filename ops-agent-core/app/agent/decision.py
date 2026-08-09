"""决策轮：异步任务观察完成后的 LLM 决策（观察 → 思考 → 行动）。

Monitor 轮询到对象终态（SUCCEEDED/FAILED/CANCELLED/STOPPED）后触发：
- 组装 plan 上下文 + 观察结果 → 单轮 LLM 推理（同一套工具协议）
- LLM 可调 plan_update（步骤 done/failed/cancelled、plan DONE/FAILED/CANCELLED）、
  suggest_action（下一步/重试 retry_of）、只读工具（补充观察）
- 写操作永远经 suggest_action → 审批 → execute，模型无法自授权
- 工具循环上限 MAX_DECISION_ROUNDS 防死循环
"""
import asyncio
import json
import logging
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.context import TaskContext
from app.agent.graph import (
    build_tool_prompt,
    handle_plan_create,
    handle_plan_update,
    handle_suggest_action,
    _parse_tool_calls,
)
from app.transport.grpc_client import GrpcClient
from app.tools.http_client import AdminHttpClient
from app.tools.registry import ToolRegistry

log = logging.getLogger("agent.decision")

MAX_DECISION_ROUNDS = 3

DECISION_SYSTEM = (
    "你是运维 agent 的决策模块。系统刚完成一次异步任务观察（对象状态已到达终态），"
    "请依据观察结果决定执行计划（plan）的下一步，并更新计划状态。\n"
    "规则：\n"
    "- 步骤成功：先用 plan_update 把该步骤标记为 done；若还有后续步骤，用 suggest_action "
    "（带 plan_id + step_no）提出下一步写操作建议；若全部步骤已完成，用 plan_update 将 plan 置 DONE。\n"
    "- 步骤失败：先用 plan_update 把该步骤标记为 failed（note 写明原因）；然后决定："
    "需要重试则 suggest_action（带 retry_of=原 suggestion_id）；方案不可行则 plan_update 将 plan 置 "
    "FAILED 或 CANCELLED 并说明；也可以仅汇报。\n"
    "- 写操作只能通过 suggest_action 提出，审批通过后系统执行；你无权直接执行写操作。\n"
    "- 每次只调用一个工具，等结果返回后再决定下一步；信息足够时直接输出决策说明（markdown），不要输出 JSON。\n"
    "- 严禁编造观察结果；一切基于下方给出的观察数据与工具返回。\n"
)


async def run_decision_round(llm: Any, http: AdminHttpClient, registry: ToolRegistry,
                             client: GrpcClient, store: Any, tracker: Any,
                             monitor: Any, terminal_status: str,
                             observation: str = "") -> Optional[str]:
    """执行一次决策轮。返回决策说明（无工具调用后的最终文本）。

    monitor：已到达终态的对象监视；terminal_status：终态（SUCCEEDED/FAILED/...）；
    observation：观察数据摘要（查询返回的原文，供 LLM 分析）。
    """
    plan: Optional[dict] = None
    if monitor.plan_id and store is not None:
        try:
            plan = await store.get_plan(monitor.plan_id)
        except Exception as e:  # noqa: BLE001
            log.warning("decision plan fetch failed: %s", e)

    plan_text = _format_plan(plan) or "（无关联计划）"
    human = (
        f"观察结果：对象 {monitor.object_type}/{monitor.object_id} 状态已到达 {terminal_status}。\n"
        f"观察数据：{observation or '（无）'}\n"
        f"当前计划：\n{plan_text}\n"
        f"最近完成/失败的建议：{monitor.suggestion_id or '（无）'}\n"
        "请决定计划下一步并更新状态。"
    )
    messages: list[Any] = [
        SystemMessage(content=DECISION_SYSTEM + "\n\n" + build_tool_prompt(registry)),
        HumanMessage(content=human),
    ]
    ctx = TaskContext(task_id=monitor.task_id or "", task_token=monitor.task_token or "")
    final_text = ""
    try:
        for _ in range(MAX_DECISION_ROUNDS):
            resp = await llm.ainvoke(messages)
            content = str(getattr(resp, "content", "")).strip()
            calls = _parse_tool_calls(content)
            if not calls:
                final_text = content or "（决策完成，无说明）"
                break
            tc = calls[0]
            name, args = tc["name"], tc.get("args") or {}
            await client.send_event(ctx.task_id, "tool_call",
                                    json.dumps({"name": name, "args": args}, ensure_ascii=False))
            result = await _execute_decision_tool(name, args, store, http, registry,
                                                  ctx, tracker)
            body = result.get("body") if isinstance(result, dict) else result
            summary = str(body)[:500] if body is not None else ""
            await client.send_event(ctx.task_id, "tool_result",
                                    json.dumps({"name": name, "summary": summary},
                                               ensure_ascii=False))
            messages.append(AIMessage(content=content))
            messages.append(SystemMessage(
                content=f"工具 [{name}] 返回结果：\n{json.dumps(result, ensure_ascii=False)}"))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - 决策失败不阻塞进程
        log.error("decision round failed: %s", e, exc_info=True)
        final_text = f"（决策失败：{e}）"
    if final_text:
        log.info("decision done: plan=%s obj=%s/%s -> %s", monitor.plan_id,
                 monitor.object_type, monitor.object_id, terminal_status)
    return final_text


async def _execute_decision_tool(name: str, args: dict, store: Any,
                                 http: AdminHttpClient, registry: ToolRegistry,
                                 ctx: TaskContext, tracker: Any) -> dict:
    """决策轮内的工具执行：内置工具走本地 handler，只读工具走 HTTP。"""
    if name == "plan_create":
        return await handle_plan_create(store, ctx, args) if store is not None else {
            "status": 500, "body": "plan_create unavailable"}
    if name == "plan_update":
        notify = getattr(tracker, "notify_plan", None)
        return await handle_plan_update(store, ctx, args, notify=notify) if store is not None else {
            "status": 500, "body": "plan_update unavailable"}
    if name == "suggest_action":
        return await handle_suggest_action(store, ctx, args) if store is not None else {
            "status": 500, "body": "suggest_action unavailable"}
    tool = registry.get(name)
    if tool is None:
        return {"status": 0, "body": f"unknown tool: {name}"}
    if tool.is_write:
        return {"status": 403, "body": "decision round cannot execute write tools"}
    return await http.call(tool, args, ctx)


def _format_plan(plan: Optional[dict]) -> str:
    if not plan:
        return ""
    steps = plan.get("steps") or []
    step_lines = "\n".join(
        f"  step{s.get('step_no')}: {s.get('action_type')} (target={s.get('target_type')}/"
        f"{s.get('target_id')}) status={s.get('status', 'pending')}"
        + (f" note={s.get('note')}" if s.get("note") else "")
        for s in steps)
    return (f"plan_id={plan.get('plan_id')}\nsummary={plan.get('summary')}\n"
            f"status={plan.get('status')}\nsteps:\n{step_lines or '  （空）'}")
