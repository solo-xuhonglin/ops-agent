"""plan_create 内置工具测试：建 plan + 按步骤落 PENDING suggestions（系统参数注入）。"""
from __future__ import annotations

import json

from app.agent.context import TaskContext
from app.agent.graph import handle_plan_create
from app.agent.task_store import TaskStore


class FakeDb:
    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self.executed: list[tuple[str, tuple]] = []

    @property
    def enabled(self):
        return self._enabled

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetch(self, sql, *args):
        return []

    async def fetchrow(self, sql, *args):
        return None


def make_store(db: FakeDb) -> TaskStore:
    return TaskStore(db, worker_id="w1")


async def test_plan_create_builds_plan_and_suggestions():
    db = FakeDb()
    store = make_store(db)
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="conv1")
    result = await handle_plan_create(store, ctx, {
        "summary": "训练并部署",
        "steps": [
            {"action_type": "training_create", "target_type": "dataset",
             "target_id": 96, "params": {"name": "x"}, "reason": "r1", "priority": "HIGH"},
            {"action_type": "serving_deploy", "target_type": "model_version",
             "target_id": 0, "reason": "部署训练产出", "priority": "HIGH"},
        ],
    })
    assert result["status"] == 200
    body = json.loads(result["body"])
    assert body["plan_id"] and body["steps"] == 2
    assert len(body["suggestion_ids"]) == 2

    plan_sql = [args for sql, args in db.executed if "INSERT INTO agent_plans" in sql]
    assert plan_sql and plan_sql[0][1] == "conv1"  # conversation_id 由系统注入

    sug_sql = [args for sql, args in db.executed if "INSERT INTO agent_suggestions" in sql]
    assert len(sug_sql) == 2
    first = sug_sql[0]
    assert first[1] == body["plan_id"]     # plan_id 关联
    assert first[2] == 1                   # step_no 顺序
    assert first[4] == "conv1"             # conversation_id 注入
    assert first[5] == "training_create"   # LLM 填的业务参数
    assert first[7] == 96


async def test_plan_create_skips_invalid_steps():
    db = FakeDb()
    store = make_store(db)
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="conv1")
    result = await handle_plan_create(store, ctx, {
        "summary": "s",
        "steps": [{"action_type": "training_create"}, {}, {"action_type": "training_delete"}],
    })
    body = json.loads(result["body"])
    assert body["steps"] == 2  # 空 dict 步骤被跳过


async def test_plan_create_store_disabled_returns_error():
    db = FakeDb(enabled=False)
    store = make_store(db)
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="conv1")
    result = await handle_plan_create(store, ctx, {"summary": "s", "steps": []})
    assert result["status"] == 500


async def test_suggest_action_creates_pending_suggestion():
    from app.agent.graph import handle_suggest_action

    db = FakeDb()
    store = make_store(db)
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="conv1")
    result = await handle_suggest_action(store, ctx, {
        "action_type": "serving_undeploy", "target_type": "serving_endpoint",
        "target_id": 3, "params": {}, "reason": "不健康", "priority": "HIGH",
    })
    assert result["status"] == 200
    body = json.loads(result["body"])
    assert body["suggestion_id"].startswith("sug_")

    sug_sql = [args for sql, args in db.executed if "INSERT INTO agent_suggestions" in sql]
    assert len(sug_sql) == 1
    args = sug_sql[0]
    assert args[3] == "t1"        # source_task_id 注入
    assert args[4] == "conv1"     # conversation_id 注入
    assert args[5] == "serving_undeploy"
    assert args[7] == 3
    assert args[9] == "不健康"
    assert args[10] == "HIGH"


async def test_suggest_action_requires_action_type():
    from app.agent.graph import handle_suggest_action

    db = FakeDb()
    store = make_store(db)
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="conv1")
    result = await handle_suggest_action(store, ctx, {"target_id": 3})
    assert result["status"] == 400
    assert db.executed == []  # 未落库


async def test_suggest_action_store_disabled_returns_error():
    from app.agent.graph import handle_suggest_action

    db = FakeDb(enabled=False)
    store = make_store(db)
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="conv1")
    result = await handle_suggest_action(store, ctx, {"action_type": "training_delete"})
    assert result["status"] == 500
