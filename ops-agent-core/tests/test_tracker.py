"""TaskTracker v3 直写库测试：Plan 持久化、监视推进、plan_update 通知。

重点回归：此前 send_plan/send_async_suggestion 漏 await 导致 Plan 从不落库、
下一步建议从不推送——本测试用 FakeStore 断言直写库调用确实发生。
"""
from __future__ import annotations

import pytest

from app.agent.task_store import TaskStore
from app.agent.tracker import TaskTracker


class FakeDatabase:
    """记录型 DB mock：不执行真实 SQL，只记录调用。"""

    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self.executed: list[tuple[str, tuple]] = []
        self.fetched: list[tuple[str, tuple]] = []
        self.fetched_rows: dict[str, list[dict] | dict | None] = {}

    @property
    def enabled(self):
        return self._enabled

    async def execute(self, sql, *args):
        if not self._enabled:
            return
        self.executed.append((sql, args))

    async def fetch(self, sql, *args):
        if not self._enabled:
            return []
        self.fetched.append((sql, args))
        for key, val in self.fetched_rows.items():
            if key in sql:
                return list(val) if isinstance(val, list) else [val]
        return []

    async def fetchrow(self, sql, *args):
        if not self._enabled:
            return None
        self.fetched.append((sql, args))
        for key, val in self.fetched_rows.items():
            if key in sql:
                return val
        return None


class FakeHttp:
    def __init__(self, status: str | None = "SUCCEEDED"):
        self.status = status

    async def call(self, tool, args, ctx):
        body = '{"data": {"status": "%s"}}' % self.status if self.status else "not json"
        return {"status": 200, "body": body}


class FakeRegistry:
    def __init__(self, has_tool: bool = True):
        self.has_tool = has_tool

    def get(self, name):
        return object() if self.has_tool else None


class FakeClient:
    def __init__(self):
        self.events: list[tuple[str, str, str]] = []  # (task_id, type, content)

    async def send_event(self, task_id, event_type, content):
        self.events.append((task_id, event_type, content))


def make_tracker(db: FakeDatabase, http_status: str | None = "SUCCEEDED") -> TaskTracker:
    store = TaskStore(db, worker_id="w1")
    client = FakeClient()
    registry = FakeRegistry()
    return TaskTracker(store, FakeHttp(http_status), client, registry)


async def test_upsert_plan_persists_to_store():
    """核心回归：upsert_plan 必须真实写入 store（此前漏 await 导致从不落库）。"""
    db = FakeDatabase()
    tracker = make_tracker(db)
    plan = {"plan_id": "plan_x", "conversation_id": "conv1",
            "summary": "训练并部署", "status": "RUNNING"}
    await tracker.upsert_plan(plan)
    inserts = [args for sql, args in db.executed if "agent_plans" in sql]
    assert inserts, "plan 未写入 agent_plans（漏 await 回归）"
    assert inserts[0][0] == "plan_x"


async def test_upsert_plan_disabled_db_noop():
    db = FakeDatabase(enabled=False)
    tracker = make_tracker(db)
    await tracker.upsert_plan({"plan_id": "p", "conversation_id": "c"})
    assert db.executed == []


async def test_monitor_reached_advances_plan_with_pending():
    """目标达成：suggestion 置 EXECUTED + plan 有下一步时发 plan_update(RUNNING)。"""
    db = FakeDatabase()
    db.fetched_rows = {
        "FROM agent_plans": {"plan_id": "plan1", "conversation_id": "conv1", "summary": "s"},
        "agent_suggestions WHERE plan_id": [{"action_type": "serving_deploy"}],
    }
    tracker = make_tracker(db)
    tracker.register(object_type="training_job", object_id=32, conversation_id="conv1",
                     task_id="t1", task_token="tok", query_tool="training_get",
                     query_args={"jobId": 32}, plan_id="plan1",
                     suggestion_id="sug1", action_type="training_create")
    await tracker._check(next(iter(tracker._monitors.values())))

    updates = [args for sql, args in db.executed if "agent_suggestions SET status" in sql]
    assert updates and updates[0][1] == "EXECUTED", f"suggestion 未置 EXECUTED: {updates}"
    plan_updates = [args for sql, args in db.executed if "UPDATE agent_plans" in sql]
    assert plan_updates and plan_updates[0][1] == "RUNNING", f"plan 未推进: {plan_updates}"
    assert any("plan_update" in e[1] for e in tracker.client.events), "未发送 plan_update 事件"


async def test_monitor_reached_plan_done_when_no_pending():
    db = FakeDatabase()
    db.fetched_rows = {
        "FROM agent_plans": {"plan_id": "plan1", "conversation_id": "conv1", "summary": "s"},
        "agent_suggestions WHERE plan_id": [],
    }
    tracker = make_tracker(db)
    tracker.register(object_type="training_job", object_id=32, conversation_id="conv1",
                     task_id="t1", task_token="tok", query_tool="training_get",
                     query_args={"jobId": 32}, plan_id="plan1",
                     suggestion_id="sug1", action_type="training_create")
    await tracker._check(next(iter(tracker._monitors.values())))
    plan_updates = [args for sql, args in db.executed if "UPDATE agent_plans" in sql]
    assert plan_updates and plan_updates[0][1] == "DONE", f"plan 未完结: {plan_updates}"


async def test_monitor_failed_marks_failed():
    db = FakeDatabase()
    db.fetched_rows = {
        "FROM agent_plans": {"plan_id": "plan1", "conversation_id": "conv1", "summary": "s"},
    }
    tracker = make_tracker(db, http_status="FAILED")
    tracker.register(object_type="training_job", object_id=32, conversation_id="conv1",
                     task_id="t1", task_token="tok", query_tool="training_get",
                     query_args={"jobId": 32}, plan_id="plan1",
                     suggestion_id="sug1", action_type="training_create")
    await tracker._check(next(iter(tracker._monitors.values())))
    sug_updates = [args for sql, args in db.executed if "agent_suggestions SET status" in sql]
    assert sug_updates and sug_updates[0][1] == "FAILED"
    plan_updates = [args for sql, args in db.executed if "UPDATE agent_plans" in sql]
    assert plan_updates and plan_updates[0][1] == "FAILED"


async def test_monitor_query_tool_missing_marks_finished():
    db = FakeDatabase()
    tracker = make_tracker(db)
    tracker.registry = FakeRegistry(has_tool=False)
    tracker.register(object_type="training_job", object_id=1, conversation_id="c",
                     task_id="t", task_token="k", query_tool="training_get",
                     query_args={}, plan_id="", suggestion_id="", action_type="training_create")
    mon = next(iter(tracker._monitors.values()))
    await tracker._check(mon)
    assert mon.finished


async def test_no_unawaited_coroutine_warning():
    """回归：所有 client/store 异步调用均被 await（无 RuntimeWarning: coroutine was never awaited）。"""
    db = FakeDatabase()
    tracker = make_tracker(db)
    await tracker.upsert_plan({"plan_id": "p1", "conversation_id": "c1", "status": "RUNNING"})
    assert db.executed
