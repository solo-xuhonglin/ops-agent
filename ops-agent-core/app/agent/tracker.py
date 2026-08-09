"""Agent 侧任务跟踪（Plan + 异步轮询 + 自主推进）。

流程控制完全在 agent 侧：
- Plan：第一次对话建立（conversation 级步骤列表），每步执行后更新；经 gRPC task_plan 持久化到 admin
- 轮询：写接口返回 object_id 后注册监视，按 10s 起步指数退避（×2，5m 封顶）调业务查询
- 推进：目标状态达成 → 更新 Plan → 有下一步则 gRPC async_suggestion 上报（admin 落 PENDING 待审批）
凭证：复用执行任务的 scoped token（admin 对 suggestion_id>0 任务签发长 TTL）
"""
import asyncio
import json
import logging
import time
from typing import Any, Callable, Optional

from app.transport import agent_pb2
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
                 step_action: str, target_status: str,
                 next_step: Optional[dict] = None) -> None:
        self.object_type = object_type
        self.object_id = object_id
        self.conversation_id = conversation_id
        self.task_id = task_id            # 来源任务（async_suggestion 的 task_id）
        self.task_token = task_token      # 长 TTL scoped token（轮询查询凭证）
        self.query_tool = query_tool      # training_get / serving_get ...
        self.query_args = query_args      # {"jobId": 32} / {"endpointId": 7}
        self.step_action = step_action    # 当前正在跟踪的 Plan 步骤（达成后标记 done）
        self.target_status = target_status
        self.next_step = next_step        # 达成后上报的 async_suggestion（None=只记录）
        self.check_interval = BASE_INTERVAL_S
        self.last_checked = 0.0
        self.finished = False
        self.succeeded = False


class TaskTracker:
    """Plan 持久化 + 后台轮询 + 自主推进（agent 进程内单例，main 装配）。"""

    def __init__(self, http: AdminHttpClient, client: GrpcClient,
                 registry: ToolRegistry) -> None:
        self.http = http
        self.client = client
        self.registry = registry
        self._monitors: dict[str, Monitor] = {}
        self._plans: dict[str, dict] = {}   # conversation_id -> plan dict
        self._task: Optional[asyncio.Task] = None

    # ==================== Plan ====================

    def upsert_plan(self, plan: dict) -> None:
        """内存更新 + gRPC 上报持久化（admin 仅落库）。plan 结构见 tracker.md。"""
        conv = plan.get("conversation_id") or ""
        if not conv:
            return
        self._plans[conv] = plan
        try:
            self.client.send_plan(plan)
        except Exception as e:  # noqa: BLE001 - 上报失败不阻塞任务
            log.warning("plan sync failed: conversation=%s err=%s", conv, e)

    def get_plan(self, conversation_id: str) -> Optional[dict]:
        return self._plans.get(conversation_id)

    def update_step(self, conversation_id: str, action_type: str, **patch) -> None:
        plan = self._plans.get(conversation_id)
        if not plan:
            return
        for step in plan.get("steps", []):
            if step.get("action_type") == action_type:
                step.update(patch)
                break
        self.upsert_plan(plan)

    def next_step_for(self, conversation_id: str, current_action: str) -> Optional[dict]:
        """Plan 中当前步骤之后的第一个待执行步骤（作 async_suggestion 上报内容）。"""
        plan = self._plans.get(conversation_id)
        if not plan:
            return None
        steps = plan.get("steps", [])
        idx = next((i for i, s in enumerate(steps)
                    if s.get("action_type") == current_action), -1)
        for s in steps[idx + 1:]:
            if s.get("status") in ("pending", "awaiting_approval"):
                return s
        return None

    # ==================== 轮询 ====================

    def register(self, object_type: str, object_id: int, conversation_id: str,
                 task_id: str, task_token: str, query_tool: str, query_args: dict,
                 step_action: str, target_status: str = "SUCCEEDED",
                 next_step: Optional[dict] = None) -> None:
        key = f"{object_type}:{object_id}"
        monitor = Monitor(object_type, object_id, conversation_id, task_id,
                          task_token, query_tool, query_args, step_action,
                          target_status, next_step)
        self._monitors[key] = monitor
        log.info("monitor registered: %s step=%s target=%s next=%s", key, step_action,
                 target_status, next_step and next_step.get("action_type"))

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
            await self._on_done(monitor)
        elif status in ("FAILED", "CANCELLED", "STOPPED"):
            monitor.finished = True
            log.info("monitor terminal failure: %s/%s status=%s",
                     monitor.object_type, monitor.object_id, status)
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

    async def _on_done(self, monitor: Monitor) -> None:
        # 更新 Plan：当前步骤标记 done + 记录结果对象
        if monitor.conversation_id:
            self.update_step(monitor.conversation_id, monitor.step_action,
                             status="done", object_type=monitor.object_type,
                             object_id=monitor.object_id)
        # 有下一步则上报 async_suggestion（admin 落 PENDING 待审批）
        if monitor.next_step:
            try:
                self.client.send_async_suggestion(monitor.conversation_id, monitor.task_id,
                                                  **monitor.next_step)
                log.info("async suggestion sent: conv=%s action=%s",
                         monitor.conversation_id, monitor.next_step.get("action_type"))
            except Exception as e:  # noqa: BLE001
                log.warning("async suggestion send failed: %s", e)
