"""Agent 侧任务跟踪（Plan + 异步轮询 + 自主推进，全部直写库）。

模型：
- plan/suggestion/task 业务行由 worker 直写 agent_plans/agent_suggestions/agent_tasks（asyncpg）
- 轮询：写接口返回 object_id 后注册监视，指数退避（10s 起步 ×2，5m 封顶）调业务查询
- 推进：目标达成 → 当前 suggestion 置 EXECUTED → 检查 plan 剩余 PENDING 步骤 →
  有则通知前端（plan_update 事件），无则 plan DONE
- 变更通知：TaskEvent(type=plan_update) 经 gRPC 上报 → admin 落对话消息 + SSE 刷新
"""
import asyncio
import json
import logging
import time
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.graph import _extract_conclusion, _format_plan_summary, build_graph, run_graph
from app.agent.task_store import TaskStore
from app.transport.grpc_client import GrpcClient
from app.tools.http_client import AdminHttpClient, TaskContext
from app.tools.registry import ToolRegistry

log = logging.getLogger("agent.tracker")

# 轮询退避：起步间隔与封顶
BASE_INTERVAL_S = 10.0
MAX_INTERVAL_S = 300.0
SCAN_TICK_S = 5.0

# 推进轮：决策图轮数上限 + 任务 id 前缀（admin 据此绑定会话）
ADVANCE_MAX_ROUNDS = 6
ADVANCE_TASK_PREFIX = "plan_advance:"

ADVANCE_SYSTEM = (
    "你是运维 agent 的决策模块。系统刚完成一次异步任务观察（对象状态已到达终态），"
    "请依据观察结果决定执行计划（plan）的下一步，并更新计划状态。\n"
    "规则：\n"
    "- 步骤成功：先用 plan_update 把该步骤标记为 done；若还有后续步骤，用 approve_<写操作名> "
    "（带 plan_id + step_no）提出下一步写操作审批建议；若全部步骤已完成，用 plan_update 将 plan 置 DONE。\n"
    "- 步骤失败：先用 plan_update 把该步骤标记为 failed（note 写明原因）；然后决定："
    "需要重试则 approve_<写操作名>（带 retry_of=原 suggestion_id）；方案不可行则 plan_update 将 plan 置 "
    "FAILED 或 CANCELLED 并说明；也可以仅汇报。\n"
    "- 写操作只能通过 approve_<写操作名> 提出审批建议，审批通过后系统执行；你无权直接执行写操作。\n"
    "- 需要确认对象状态时可调用 wait_until 或只读查询工具；信息足够时直接输出决策说明（markdown）。\n"
    "- 严禁编造观察结果；一切基于下方给出的观察数据与工具返回。\n"
)


class Monitor:
    """单个异步对象的状态监视（训练 job / serving endpoint 等）。"""

    def __init__(self, object_type: str, object_id: int, conversation_id: str,
                 task_id: str, task_token: str, query_tool: str, query_args: dict,
                 plan_id: str, suggestion_id: str, action_type: str,
                 target_status: str = "SUCCEEDED") -> None:
        self.object_type = object_type
        self.object_id = object_id
        self.conversation_id = conversation_id
        self.task_id = task_id            # 来源任务（execute 轮）
        self.task_token = task_token      # 长 TTL scoped token（轮询查询凭证）
        self.query_tool = query_tool      # training_get / serving_get ...
        self.query_args = query_args      # {"jobId": 32} / {"endpointId": 7}
        self.plan_id = plan_id            # 所属 plan（可能为空）
        self.suggestion_id = suggestion_id  # 当前跟踪的 suggestion（达成后置 EXECUTED）
        self.action_type = action_type    # 当前步骤动作（training_create ...）
        self.target_status = target_status
        self.check_interval = BASE_INTERVAL_S
        self.last_checked = 0.0
        self.finished = False
        self.succeeded = False


class TaskTracker:
    """Plan 直写库 + 后台轮询 + 决策轮（agent 进程内单例，main 装配）。"""

    def __init__(self, store: TaskStore, http: AdminHttpClient, client: GrpcClient,
                 registry: ToolRegistry, llm: Any = None) -> None:
        self.store = store
        self.http = http
        self.client = client
        self.registry = registry
        self.llm = llm  # 决策轮用（异步观察完成后由 LLM 决定 plan 下一步）
        self._monitors: dict[str, Monitor] = {}
        self._task: Optional[asyncio.Task] = None

    # ==================== Plan（直写库）====================

    async def upsert_plan(self, plan: dict) -> None:
        """agent 建立/修改 plan（直写 agent_plans）。plan 结构见设计文档。"""
        try:
            await self.store.upsert_plan(plan)
        except Exception as e:  # noqa: BLE001 - DB 失败不阻塞任务
            log.warning("plan persist failed: %s", e)

    async def update_plan_status(self, plan_id: str, status: str, message: str = "") -> None:
        """plan 状态变更 + 通知前端。"""
        if not plan_id:
            return
        try:
            await self.store.update_plan_status(plan_id, status)
        except Exception as e:  # noqa: BLE001
            log.warning("plan status update failed: %s", e)
            return
        await self._notify_plan(plan_id, status, message)

    async def notify_plan(self, plan_id: str, status: str, message: str = "") -> None:
        """仅通知前端 plan 变更（状态已由调用方落库，如 plan_update 工具）。"""
        await self._notify_plan(plan_id, status, message)

    async def _notify_plan(self, plan_id: str, status: str, message: str = "") -> None:
        """plan_update 事件上报：admin 据此落对话消息 + SSE 通知前端刷新 plan 卡片。"""
        try:
            plan = await self.store.get_plan(plan_id)
        except Exception as e:  # noqa: BLE001
            log.warning("plan fetch failed for notify: %s", e)
            return
        if not plan:
            return
        payload = json.dumps({
            "planId": plan_id,
            "conversationId": plan.get("conversation_id", ""),
            "status": status,
            "summary": plan.get("summary", ""),
            "message": message,
        }, ensure_ascii=False)
        try:
            # task_id 用 plan_id 占位；admin 从 content.conversationId 定位会话（见 AgentGrpcService.handlePlanUpdate）
            await self.client.send_event(plan_id, "plan_update", payload)
            log.info("plan_update sent: plan=%s status=%s", plan_id[:8], status)
        except Exception as e:  # noqa: BLE001
            log.warning("plan_update send failed: %s", e)

    # ==================== 轮询 ====================

    def register(self, object_type: str, object_id: int, conversation_id: str,
                 task_id: str, task_token: str, query_tool: str, query_args: dict,
                 plan_id: str = "", suggestion_id: str = "", action_type: str = "",
                 target_status: str = "SUCCEEDED") -> None:
        key = f"{object_type}:{object_id}"
        monitor = Monitor(object_type, object_id, conversation_id, task_id,
                          task_token, query_tool, query_args, plan_id,
                          suggestion_id, action_type, target_status)
        self._monitors[key] = monitor
        log.info("monitor registered: %s step=%s target=%s", key, action_type, target_status)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            log.info("task tracker loop started")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                for monitor in list(self._monitors.values()):
                    if monitor.finished:
                        continue
                    if time.monotonic() - monitor.last_checked < monitor.check_interval:
                        continue
                    try:
                        await self._check(monitor)
                    except Exception as e:  # noqa: BLE001
                        log.warning("monitor check failed: %s/%s err=%s",
                                    monitor.object_type, monitor.object_id, e)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("tracker loop error: %s", e)
            await asyncio.sleep(SCAN_TICK_S)

    async def _check(self, monitor: Monitor) -> None:
        monitor.last_checked = time.monotonic()
        tool = self.registry.get(monitor.query_tool)
        if tool is None:
            log.warning("monitor query tool missing: %s", monitor.query_tool)
            monitor.finished = True
            return
        ctx = TaskContext(task_id=monitor.task_id, task_token=monitor.task_token)
        result = await self.http.call(tool, dict(monitor.query_args), ctx)
        status = self._extract_status(result)
        if status is None:
            return  # 响应异常/查询失败：等下轮
        if status == monitor.target_status:
            monitor.finished = True
            monitor.succeeded = True
            log.info("monitor target reached: %s/%s status=%s",
                     monitor.object_type, monitor.object_id, status)
            await self._on_done(monitor, observation=str(result.get("body", ""))[:2000])
        elif status in ("FAILED", "CANCELLED", "STOPPED", "INVALID"):
            monitor.finished = True
            log.info("monitor terminal failure: %s/%s status=%s",
                     monitor.object_type, monitor.object_id, status)
            await self._on_failed(monitor, status,
                                  observation=str(result.get("body", ""))[:2000])
        else:
            # 指数退避
            monitor.check_interval = min(monitor.check_interval * 2, MAX_INTERVAL_S)

    @staticmethod
    def _extract_status(result: dict) -> Optional[str]:
        body = result.get("body")
        if not isinstance(body, str):
            return None
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None
        node = data.get("data") if isinstance(data, dict) else None
        if isinstance(node, dict):
            return node.get("status")
        if isinstance(node, list) and node:
            return node[0].get("status")
        return None

    async def _run_advance(self, monitor: Monitor, terminal_status: str,
                           observation: str = "") -> None:
        """推进轮：对象到终态后 worker 自治发起决策图，模型决定 plan 下一步。

        不经 admin dispatch；task_id=plan_advance:{plan_id}，先发 plan_advance 事件
        （admin 据此 bindTask + 落 assistant 消息），后续事件/结论走现有通道。
        """
        if not monitor.plan_id or self.llm is None or self.store is None or not self.store.enabled:
            return
        task_id = f"{ADVANCE_TASK_PREFIX}{monitor.plan_id}"
        try:
            payload = json.dumps({
                "conversationId": monitor.conversation_id,
                "planId": monitor.plan_id,
                "status": terminal_status,
                "message": f"对象 {monitor.object_type}/{monitor.object_id} "
                           f"已到达 {terminal_status}，计划推进中",
            }, ensure_ascii=False)
            await self.client.send_event(task_id, "plan_advance", payload)
        except Exception as e:  # noqa: BLE001
            log.warning("plan_advance event failed: %s", e)
        try:
            plan = await self.store.get_plan(monitor.plan_id)
            plan_text = _format_plan_summary(plan)
        except Exception as e:  # noqa: BLE001
            log.warning("advance plan fetch failed: %s", e)
            plan_text = ""
        human = (
            f"观察结果：对象 {monitor.object_type}/{monitor.object_id} "
            f"状态已到达 {terminal_status}。\n"
            f"观察数据：{observation or '（无）'}\n"
            f"当前计划：\n{plan_text or '（无关联计划）'}\n"
            f"最近完成/失败的建议：{monitor.suggestion_id or '（无）'}\n"
            "请决定计划下一步并更新状态。"
        )
        ctx = TaskContext(task_id=task_id, task_token=monitor.task_token,
                          conversation_id=monitor.conversation_id)
        conclusion = ""
        try:
            graph = build_graph(llm_runtime=self.llm, http=self.http,
                                registry=self.registry, client=self.client,
                                tracker=self, store=self.store)
            final, hit_limit = await run_graph(graph, ctx,
                                    [SystemMessage(content=ADVANCE_SYSTEM),
                                     HumanMessage(content=human)],
                                    max_rounds=ADVANCE_MAX_ROUNDS)
            conclusion = _extract_conclusion(final).strip()
            if hit_limit:
                conclusion = (f"⚠️ 推进轮因工具调用轮次达到上限（{ADVANCE_MAX_ROUNDS} 轮）而停止。"
                              f"\n\n{conclusion}")
        except Exception as e:  # noqa: BLE001 - 推进轮失败不阻塞
            log.error("advance round failed: %s", e, exc_info=True)
        if not conclusion:
            conclusion = f"（对象已到达 {terminal_status}，推进决策未产出说明）"
        try:
            await self.store.insert_task(task_id, "advance", monitor.conversation_id,
                                         query=f"plan advance: {monitor.plan_id}",
                                         suggestion_id=monitor.suggestion_id)
            await self.store.finish_task(task_id, "SUCCEEDED", conclusion)
        except Exception as e:  # noqa: BLE001
            log.warning("advance task persist failed: %s", e)
        try:
            await self.client.send_result(task_id, ok=True, conclusion=conclusion)
        except Exception as e:  # noqa: BLE001
            log.warning("advance result send failed: %s", e)
        log.info("advance round done: plan=%s obj=%s/%s -> %s", monitor.plan_id,
                 monitor.object_type, monitor.object_id, terminal_status)

    async def _on_done(self, monitor: Monitor, observation: str = "") -> None:
        """目标达成：suggestion 置 EXECUTED → 推进轮（模型标记步骤 done + 决定下一步）。"""
        if monitor.suggestion_id:
            try:
                await self.store.update_suggestion_result(
                    monitor.suggestion_id, "EXECUTED",
                    f"object {monitor.object_type}/{monitor.object_id} reached {monitor.target_status}")
            except Exception as e:  # noqa: BLE001
                log.warning("suggestion result update failed: %s", e)
        if self.llm is not None and monitor.plan_id:
            await self._run_advance(monitor, "SUCCEEDED", observation)
        elif monitor.plan_id:
            # 无 LLM（单测/降级）：机械推进——下一步若有 PENDING 建议则通知，无则 DONE
            try:
                pending = await self.store.pending_steps(monitor.plan_id)
                if pending:
                    await self.update_plan_status(
                        monitor.plan_id, "RUNNING",
                        f"步骤已完成，下一步待审批：{pending[0].get('action_type')}")
                else:
                    await self.update_plan_status(monitor.plan_id, "DONE", "计划全部完成")
            except Exception as e:  # noqa: BLE001
                log.warning("plan advance failed: %s", e)

    async def _on_failed(self, monitor: Monitor, status: str,
                         observation: str = "") -> None:
        """目标失败：suggestion 置 FAILED → 推进轮（模型决定重试/调整/废弃/汇报）。"""
        if monitor.suggestion_id:
            try:
                await self.store.update_suggestion_result(
                    monitor.suggestion_id, "FAILED",
                    f"object {monitor.object_type}/{monitor.object_id} ended {status}")
            except Exception as e:  # noqa: BLE001
                log.warning("suggestion failed update: %s", e)
        if self.llm is not None and monitor.plan_id:
            await self._run_advance(monitor, status, observation)
        elif monitor.plan_id:
            try:
                await self.update_plan_status(monitor.plan_id, "FAILED",
                                              f"步骤失败：对象 {status}")
            except Exception as e:  # noqa: BLE001
                log.warning("plan failed update failed: %s", e)
