"""TaskStore（agent 自治写库 repo）测试：SQL 形状与参数（mock Database）。"""
from __future__ import annotations

import pytest

from app.agent.task_store import TaskStore
from app.db import Database


class FakeDb:
    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self.executed: list[tuple[str, tuple]] = []
        self.fetched: list[tuple[str, tuple]] = []
        self.rows: list[dict] = []
        self.row: dict | None = None

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
        return self.rows

    async def fetchrow(self, sql, *args):
        if not self._enabled:
            return None
        self.fetched.append((sql, args))
        return self.row


def make_store(db: FakeDb) -> TaskStore:
    return TaskStore(db, worker_id="w1")


async def test_insert_task():
    db = FakeDb()
    store = make_store(db)
    await store.insert_task("t1", "chat", "conv1", query="hello", plan_id="", suggestion_id="")
    sql, args = db.executed[-1]
    assert "INSERT INTO agent_tasks" in sql
    assert args[:5] == ("t1", "chat", "", "", "conv1")  # 空串由 SQL NULLIF 转 NULL


async def test_insert_execute_task_with_links():
    db = FakeDb()
    store = make_store(db)
    await store.insert_task("t2", "execute", "conv1", plan_id="plan1", suggestion_id="sug1")
    sql, args = db.executed[-1]
    assert args[:5] == ("t2", "execute", "plan1", "sug1", "conv1")


async def test_finish_task():
    db = FakeDb()
    store = make_store(db)
    await store.finish_task("t1", "SUCCEEDED", "结论", "推理")
    sql, args = db.executed[-1]
    assert "SET status=$2" in sql and args[0] == "t1" and args[1] == "SUCCEEDED"


async def test_cancel_task_only_running():
    db = FakeDb()
    store = make_store(db)
    await store.cancel_task("t1", "user stop")
    sql, args = db.executed[-1]
    assert "status IN ('DISPATCHED','RUNNING')" in sql


async def test_upsert_plan_requires_conversation():
    db = FakeDb()
    store = make_store(db)
    await store.upsert_plan({"plan_id": "p", "conversation_id": "", "status": "RUNNING"})
    assert db.executed == []  # 无会话不落库


async def test_upsert_plan_insert_and_conflict_update():
    db = FakeDb()
    store = make_store(db)
    await store.upsert_plan({"plan_id": "p1", "conversation_id": "c1",
                             "summary": "s", "status": "RUNNING"})
    sql, args = db.executed[-1]
    assert "ON CONFLICT (plan_id)" in sql
    assert args[0] == "p1" and args[3] == "RUNNING"


async def test_insert_suggestion_pending():
    db = FakeDb()
    store = make_store(db)
    sid = await store.insert_suggestion({
        "suggestion_id": "sug1", "plan_id": "plan1", "step_no": 1,
        "source_task_id": "t1", "conversation_id": "c1",
        "action_type": "training_create", "target_type": "dataset", "target_id": 96,
        "params": {"name": "x"}, "reason": "r", "priority": "HIGH"})
    assert sid == "sug1"
    sql, args = db.executed[-1]
    assert "INSERT INTO agent_suggestions" in sql
    assert args[5] == "training_create" and args[7] == 96
    assert "PENDING" in sql  # status 为 SQL 常量（非参数）


async def test_insert_suggestion_auto_id():
    db = FakeDb()
    store = make_store(db)
    sid = await store.insert_suggestion({"conversation_id": "c1",
                                         "action_type": "training_delete", "target_id": 5})
    assert sid.startswith("sug_")


async def test_update_suggestion_result_conditional():
    db = FakeDb()
    store = make_store(db)
    await store.update_suggestion_result("sug1", "EXECUTED", "done")
    sql, args = db.executed[-1]
    assert "status IN ('APPROVED','EXECUTING')" in sql
    assert args[0] == "sug1" and args[1] == "EXECUTED"


async def test_mark_suggestion_executing_conditional():
    db = FakeDb()
    store = make_store(db)
    await store.mark_suggestion_executing("sug1")
    sql, args = db.executed[-1]
    assert "status IN ('PENDING','APPROVED')" in sql


async def test_pending_steps_ordered():
    db = FakeDb()
    store = make_store(db)
    await store.pending_steps("plan1")
    sql, args = db.fetched[-1]
    assert "status='PENDING'" in sql and "ORDER BY step_no" in sql


async def test_disabled_db_noop():
    db = FakeDb(enabled=False)
    store = make_store(db)
    await store.insert_task("t", "chat", "c")
    await store.upsert_plan({"plan_id": "p", "conversation_id": "c"})
    await store.insert_suggestion({"conversation_id": "c", "action_type": "a", "target_id": 1})
    assert db.executed == []
