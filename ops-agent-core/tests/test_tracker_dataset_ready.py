"""回归：写操作成功后按对象类型映射目标终态，且 Monitor 在任务收敛时才兜底注册。

背景（2026-08-10 实测）：
- 会话 cd9f5b8a：dataset_create 执行后数据集 COLLECTING，WRITE_TRACK_MAP 无
  dataset_create → Monitor 从未注册 → 数据集 READY 后无人推进 plan step2。
- 会话 94ac02a6：Monitor 与 execute 轮模型自己的 wait_until **双轨**等待同一对象——
  monitor 到终态触发 advance 轮 + 模型 wait_until 醒来也提下一步审批 → 一条 EXECUTED、
  一条 REJECTED"已忽略"（sug_befc2d7f09bd）。

修复语义：写工具成功后**暂存** pending（ctx.pending_trackers），任务收敛时 flush：
- 对象已到终态（模型自己 wait_until 等到并推进 plan）→ 跳过注册（不双轨）
- 对象未到终态（模型 wait_until 超时/未等就收敛）→ 注册 Monitor 兜底推进

本测试覆盖：
- _maybe_register_tracker 暂存 pending，且按对象类型映射 target_status（dataset=READY）
- flush 时对象已到终态 → 跳过注册
- flush 时对象未到终态 → 注册 Monitor（dataset_create/collect/training/serving）
- flush 查询失败 → 兜底注册
- Monitor._check 把 INVALID 视为终态失败（数据集采集失败）触发 _on_failed
"""
from __future__ import annotations

import pytest

from app.agent.graph import _maybe_register_tracker, flush_pending_trackers
from app.agent.tracker import Monitor, TaskTracker
from app.agent.context import TaskContext
from app.transport import agent_pb2
from tests.test_agent_core import FakeClient


def make_tool(name, is_write=True):
    return agent_pb2.ToolSchema(
        name=name, description=f"d-{name}", parameters="{}",
        is_write=is_write, http_method="POST", path_template="/x")


class FakeStore:
    def __init__(self, suggestion=None, plan=None):
        self.enabled = True
        self._suggestion = suggestion or {"plan_id": "plan-1"}
        self._plan = plan or {"plan_id": "plan-1", "conversation_id": "c-1"}
        self.calls = []

    async def get_suggestion(self, sid):
        self.calls.append(("get_suggestion", sid))
        return self._suggestion

    async def get_active_plan(self, conversation_id):
        return self._plan


class FakeTracker:
    def __init__(self):
        self.registrations = []

    def register(self, **kwargs):
        self.registrations.append(kwargs)


class FakeHttp:
    """可编程当前状态；status=None 模拟查询失败。"""

    def __init__(self, status="READY"):
        self._status = status

    async def call(self, tool, args, ctx):
        if self._status is None:
            return {"status": 500, "body": "boom"}
        return {"status": 200, "body": f'{{"data": {{"status": "{self._status}"}}}}'}


class FakeRegistry:
    def __init__(self, tool_name="dataset_get"):
        self._tool_name = tool_name

    def get(self, name):
        return make_tool(name or self._tool_name, is_write=False)

    def all(self):
        return []


async def _pending(tool_name, body, http_status="READY"):
    """写工具成功 → 暂存 pending（不注册）；返回 (tracker, ctx)。"""
    tool = make_tool(tool_name)
    tracker = FakeTracker()
    store = FakeStore()
    ctx = TaskContext(task_id="t1", task_token="tok",
                      conversation_id="c-1", suggestion_id="sug-1")
    http = FakeHttp(status=http_status)
    await _maybe_register_tracker(tracker, store, ctx, tool,
                                  {"status": 202, "body": body})
    return tracker, ctx, http


async def _flush(tool_name, body, http_status):
    tracker, ctx, http = await _pending(tool_name, body, http_status)
    query_tool = {"dataset_create": "dataset_get", "dataset_collect": "dataset_get",
                  "training_create": "training_get",
                  "serving_deploy": "serving_get"}[tool_name]
    await flush_pending_trackers(tracker, FakeRegistry(query_tool), http, ctx)
    return tracker


async def test_dataset_create_pending_with_ready_target():
    """dataset_create 成功后先暂存 pending，target_status=READY（不立即注册）。"""
    tracker, ctx, _ = await _pending("dataset_create", '{"data": {"id": 15}}')
    assert tracker.registrations == []  # 未到收敛，不注册
    assert len(ctx.pending_trackers) == 1
    p = ctx.pending_trackers[0]
    assert p["object_type"] == "dataset"
    assert p["object_id"] == 15
    assert p["query_tool"] == "dataset_get"
    assert p["query_args"] == {"datasetId": 15}
    assert p["target_status"] == "READY", "数据集目标态应为 READY 而非 SUCCEEDED"
    assert p["plan_id"] == "plan-1"


async def test_flush_skips_when_object_already_terminal():
    """收敛时对象已到终态（模型 wait_until 自己等到了）→ 跳过注册，避免双轨。

    回归：94ac02a6 中 Monitor 触发 advance 轮 + 模型 wait_until 醒来都提下一步审批。
    """
    tracker, ctx, http = await _pending("dataset_create", '{"data": {"id": 15}}',
                                        http_status="READY")
    await flush_pending_trackers(tracker, FakeRegistry("dataset_get"), http, ctx)
    assert tracker.registrations == []
    assert ctx.pending_trackers == []


async def test_flush_registers_when_object_still_in_progress():
    """收敛时对象未到终态（模型 wait_until 超时/未等就结束）→ 注册 Monitor 兜底。"""
    tracker, ctx, http = await _pending("dataset_create", '{"data": {"id": 15}}',
                                        http_status="COLLECTING")
    await flush_pending_trackers(tracker, FakeRegistry("dataset_get"), http, ctx)
    assert len(tracker.registrations) == 1
    reg = tracker.registrations[0]
    assert reg["object_type"] == "dataset"
    assert reg["target_status"] == "READY"


async def test_flush_registers_on_query_failure():
    """收敛时状态查询失败 → 兜底注册（不因查询抖动丢 Monitor）。"""
    tracker, ctx, http = await _pending("dataset_create", '{"data": {"id": 15}}',
                                        http_status=None)
    await flush_pending_trackers(tracker, FakeRegistry("dataset_get"), http, ctx)
    assert len(tracker.registrations) == 1


async def test_dataset_collect_pending_target_ready():
    """dataset_collect（显式重新采集）同样映射 READY 目标态。"""
    tracker = await _flush("dataset_collect", '{"data": {"id": 16}}', "COLLECTING")
    assert len(tracker.registrations) == 1
    reg = tracker.registrations[0]
    assert reg["object_type"] == "dataset"
    assert reg["object_id"] == 16
    assert reg["target_status"] == "READY"


async def test_training_create_still_targets_succeeded():
    """训练任务目标态保持 SUCCEEDED（回归，防止数据集改动波及训练）。"""
    tracker = await _flush("training_create", '{"data": {"id": 32}}', "RUNNING")
    assert len(tracker.registrations) == 1
    reg = tracker.registrations[0]
    assert reg["object_type"] == "training_job"
    assert reg["target_status"] == "SUCCEEDED"


async def test_serving_deploy_still_targets_succeeded():
    """serving_deploy 目标态保持 SUCCEEDED（回归）。"""
    tracker = await _flush("serving_deploy", '{"data": {"id": 7}}', "CREATING")
    assert len(tracker.registrations) == 1
    reg = tracker.registrations[0]
    assert reg["object_type"] == "serving_endpoint"
    assert reg["target_status"] == "SUCCEEDED"


async def test_serving_deploy_skip_when_already_depoyed():
    """serving 已 DEPLOYED（模型 wait_until 等到了）→ 跳过注册（DEPLOYED 在成功集合内）。"""
    tracker = await _flush("serving_deploy", '{"data": {"id": 7}}', "DEPLOYED")
    assert tracker.registrations == []


@pytest.mark.asyncio
async def test_monitor_check_treats_invalid_as_terminal_failure():
    """数据集采集失败（INVALID）应被识别为终态失败 → suggestion FAILED + 推进轮。"""
    from tests.test_tracker_advance import FakeStore as AdvStore

    class AdvStoreEx(AdvStore):
        def __init__(self):
            super().__init__()
            self.suggestion_updates = []

        async def update_suggestion_result(self, suggestion_id, status, result_text=""):
            self.suggestion_updates.append((suggestion_id, status))

        async def pending_steps(self, plan_id):
            return []

    client = FakeClient()
    store = AdvStoreEx()
    tracker = TaskTracker(store, FakeHttp(status="INVALID"), client,
                          FakeRegistry(), llm=None)
    monitor = Monitor(
        object_type="dataset", object_id=15, conversation_id="c-1",
        task_id="exec-1", task_token="tok", query_tool="dataset_get",
        query_args={"datasetId": 15}, plan_id="plan-1", suggestion_id="sug-1",
        action_type="dataset_create", target_status="READY")

    await tracker._check(monitor)

    assert monitor.finished is True
    assert monitor.succeeded is False
    assert ("sug-1", "FAILED") in store.suggestion_updates
    # 无 LLM → 机械降级发 plan_update（失败通知）
    assert any(e[1] == "plan_update" for e in client.events)
