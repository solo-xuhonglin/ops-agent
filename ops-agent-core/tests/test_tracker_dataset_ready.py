"""回归：写操作成功后必须注册对象状态轮询（Monitor），且按对象类型映射目标终态。

背景（2026-08-10 实测）：会话 cd9f5b8a 中 dataset_create 执行后数据集处于 COLLECTING，
execute 任务里的模型因 wait_until/sleep 未注册（另见 test_build_openai_tools.py）只能汇报"采集中"，
且 WRITE_TRACK_MAP 没有 dataset_create → 后台 Monitor 从未注册 → 数据集 READY 后无人推进 plan
step2 训练审批 → 任务停在 step1 done 没有继续（"没有推进完成"）。

本测试覆盖：
- dataset_create / dataset_collect 写成功后注册 Monitor，且 target_status=READY
- training_create / serving_deploy 目标态仍为 SUCCEEDED
- Monitor._check 把 INVALID 视为终态失败（数据集采集失败）触发 _on_failed
"""
from __future__ import annotations

import pytest

from app.agent.graph import _maybe_register_tracker
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
    def __init__(self, status="READY"):
        self._status = status

    async def call(self, tool, args, ctx):
        return {"status": 200, "body": f'{{"data": {{"status": "{self._status}"}}}}'}


class FakeRegistry:
    def get(self, name):
        # _check 需要 registry.get(query_tool) 返回真实工具；这里返回一个可调用对象即可
        return make_tool(name or "dataset_get", is_write=False)

    def all(self):
        return []


async def _register(tool_name, body):
    tool = make_tool(tool_name)
    tracker = FakeTracker()
    store = FakeStore()
    ctx = TaskContext(task_id="t1", task_token="tok",
                      conversation_id="c-1", suggestion_id="sug-1")
    await _maybe_register_tracker(tracker, store, ctx, tool,
                                  {"status": 202, "body": body})
    return tracker


async def test_dataset_create_registers_monitor_with_ready_target():
    """dataset_create 成功后必须注册 Monitor 且 target_status=READY（否则采集完成后无人推进 plan）。"""
    tracker = await _register("dataset_create", '{"data": {"id": 15}}')
    assert len(tracker.registrations) == 1
    reg = tracker.registrations[0]
    assert reg["object_type"] == "dataset"
    assert reg["object_id"] == 15
    assert reg["query_tool"] == "dataset_get"
    assert reg["query_args"] == {"datasetId": 15}
    assert reg["target_status"] == "READY", "数据集目标态应为 READY 而非 SUCCEEDED"
    assert reg["plan_id"] == "plan-1"


async def test_dataset_collect_registers_monitor_with_ready_target():
    """dataset_collect（显式重新采集）同样注册 READY 目标态 Monitor。"""
    tracker = await _register("dataset_collect", '{"data": {"id": 16}}')
    assert len(tracker.registrations) == 1
    reg = tracker.registrations[0]
    assert reg["object_type"] == "dataset"
    assert reg["object_id"] == 16
    assert reg["target_status"] == "READY"


async def test_training_create_still_targets_succeeded():
    """训练任务目标态保持 SUCCEEDED（回归，防止数据集改动波及训练）。"""
    tracker = await _register("training_create", '{"data": {"id": 32}}')
    reg = tracker.registrations[0]
    assert reg["object_type"] == "training_job"
    assert reg["target_status"] == "SUCCEEDED"


async def test_serving_deploy_still_targets_succeeded():
    """serving_deploy 目标态保持 SUCCEEDED（回归）。"""
    tracker = await _register("serving_deploy", '{"data": {"id": 7}}')
    reg = tracker.registrations[0]
    assert reg["object_type"] == "serving_endpoint"
    assert reg["target_status"] == "SUCCEEDED"


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
