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

from app.agent.task_store import TaskStore
from app.transport.grpc_client import GrpcClient
from app.tools.http_client import AdminHttpClient, TaskContext
from app.tools.registry import ToolRegistry

log = logging.getLogger("agent.tracker")

# 轮询退避：起步间隔与封顶
BASE_INTERVAL_S = 10.0
MAX_INTERVAL_S = 300.0
SCAN_TICK_S = 5.0


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
        elif status in ("FAILED", "CANCELLED", "STOPPED"):
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

    async def _on_done(self, monitor: Monitor, observation: str = "") -> None:
        """目标达成：suggestion 置 EXECUTED → 触发决策轮（模型标记步骤 done + 决定下一步）。"""
        if monitor.suggestion_id:
            try:
                await self.store.update_suggestion_result(
                    monitor.suggestion_id, "EXECUTED",
                    f"object {monitor.object_type}/{monitor.object_id} reached {monitor.target_status}")
            except Exception as e:  # noqa: BLE001
                log.warning("suggestion result update failed: %s", e)
        if self.llm is not None and monitor.plan_id:
            from app.agent.decision import run_decision_round
            await run_decision_round(self.llm, self.http, self.registry, self.client,
                                     self.store, self, monitor, "SUCCEEDED", observation)
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
        """目标失败：suggestion 置 FAILED → 触发决策轮（模型决定重试/调整/废弃/汇报）。"""
        if monitor.suggestion_id:
            try:
                await self.store.update_suggestion_result(
                    monitor.suggestion_id, "FAILED",
                    f"object {monitor.object_type}/{monitor.object_id} ended {status}")
            except Exception as e:  # noqa: BLE001
                log.warning("suggestion failed update: %s", e)
        if self.llm is not None and monitor.plan_id:
            from app.agent.decision import run_decision_round
            await run_decision_round(self.llm, self.http, self.registry, self.client,
                                     self.store, self, monitor, status, observation)
        elif monitor.plan_id:
            await self.update_plan_status(monitor.plan_id, "FAILED",
                                          f"步骤失败：对象 {status}")
