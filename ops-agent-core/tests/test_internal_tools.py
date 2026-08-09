"""plan_create / plan_update / suggest_action 内置工具测试。

v4 语义：plan_create 只建规划备忘录（零建议副作用）；plan_update 由模型掌舵
plan 生命周期；suggest_action 提出写操作建议（可挂 plan_id/step_no/retry_of）。
"""
from __future__ import annotations

import json

from app.agent.context import TaskContext
from app.agent.graph import handle_plan_create, handle_plan_update, handle_suggest_action
from app.agent.task_store import TaskStore


class FakeDb:
    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self.executed: list[tuple[str, tuple]] = []
        self.plan_row: dict | None = None

    @property
    def enabled(self):
        return self._enabled

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if "INSERT INTO agent_plans" in sql:
            self.plan_row = {
                "plan_id": args[0], "conversation_id": args[1],
                "summary": args[2], "steps": args[3], "status": args[4],
            }

    async def fetch(self, sql, *args):
        return []

    async def fetchrow(self, sql, *args):
        if self.plan_row is not None and "FROM agent_plans" in sql:
            return dict(self.plan_row)
        return None


def make_store(db: FakeDb) -> TaskStore:
    return TaskStore(db, worker_id="w1")


async def test_plan_create_zero_suggestion_side_effect():
    """plan_create 只建 plan，不落任何 suggestion（v4 关键回归）。"""
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
    assert "instruction" in body

    sug_sql = [sql for sql, _ in db.executed if "INSERT INTO agent_suggestions" in sql]
    assert sug_sql == []  # 零建议副作用

    plan_sql = [args for sql, args in db.executed if "INSERT INTO agent_plans" in sql]
    assert plan_sql and plan_sql[0][1] == "conv1"  # conversation_id 注入

    # steps 存进 plan.steps JSON，含 step_no/status（模型掌舵状态）
    stored = json.loads(plan_sql[0][3])
    assert len(stored) == 2
    assert stored[0]["step_no"] == 1 and stored[0]["status"] == "pending"
    assert stored[1]["action_type"] == "serving_deploy" and stored[1]["step_no"] == 2


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


async def test_plan_update_step_status():
    """plan_update 更新步骤状态（done）+ 触发通知。"""
    db = FakeDb()
    store = make_store(db)
    await handle_plan_create(store, TaskContext(task_id="t1", task_token="tok",
                                                conversation_id="conv1"),
                             {"summary": "s", "steps": [{"action_type": "training_create"}]})
    plan_id = db.plan_row["plan_id"]
    notified = []

    async def notify(pid, status, message):
        notified.append((pid, status, message))

    result = await handle_plan_update(store, TaskContext(task_id="t1", task_token="tok"),
                                      {"plan_id": plan_id, "step_no": 1, "step_status": "done",
                                       "note": "训练完成"},
                                      notify=notify)
    assert result["status"] == 200
    body = json.loads(result["body"])
    assert body["step_status"] == "done"
    assert notified and notified[0][0] == plan_id
    # steps JSON 内第 1 步置 done + note
    upd = [args for sql, args in db.executed if "UPDATE agent_plans SET steps" in sql]
    assert upd
    steps = json.loads(upd[-1][1])
    assert steps[0]["status"] == "done" and steps[0]["note"] == "训练完成"


async def test_plan_update_plan_status():
    """plan_update 更新 plan 整体状态（DONE）。"""
    db = FakeDb()
    store = make_store(db)
    await handle_plan_create(store, TaskContext(task_id="t1", task_token="tok",
                                                conversation_id="conv1"),
                             {"summary": "s", "steps": [{"action_type": "training_create"}]})
    plan_id = db.plan_row["plan_id"]
    notified = []

    async def notify(pid, status, message):
        notified.append((pid, status, message))

    result = await handle_plan_update(store, TaskContext(task_id="t1", task_token="tok"),
                                      {"plan_id": plan_id, "status": "DONE", "note": "全部完成"},
                                      notify=notify)
    assert result["status"] == 200
    upd = [args for sql, args in db.executed if "SET status=$2" in sql and plan_id == args[0]]
    assert upd and upd[-1][1] == "DONE"
    assert notified and notified[0][1] == "DONE"


async def test_plan_update_validation():
    db = FakeDb()
    store = make_store(db)
    ctx = TaskContext(task_id="t1", task_token="tok")
    assert (await handle_plan_update(store, ctx, {}))["status"] == 400          # 缺 plan_id
    assert (await handle_plan_update(store, ctx, {"plan_id": "p1", "step_no": 1,
                                                  "step_status": "bad"}))["status"] == 400
    assert (await handle_plan_update(store, ctx, {"plan_id": "p1",
                                                  "status": "PENDING"}))["status"] == 400


async def test_suggest_action_creates_pending_suggestion():
    db = FakeDb()
    store = make_store(db)
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="conv1")
    result = await handle_suggest_action(store, ctx, {
        "action_type": "serving_undeploy", "target_type": "serving_endpoint",
        "target_id": 3, "params": {}, "reason": "不健康", "priority": "HIGH",
        "plan_id": "plan_123", "step_no": 2,
    })
    assert result["status"] == 200
    body = json.loads(result["body"])
    assert body["suggestion_id"].startswith("sug_")

    sug_sql = [args for sql, args in db.executed if "INSERT INTO agent_suggestions" in sql]
    assert len(sug_sql) == 1
    args = sug_sql[0]
    assert args[1] == "plan_123"  # plan_id 关联
    assert args[2] == 2           # step_no
    assert args[4] == "conv1"     # conversation_id 注入
    assert args[5] == "serving_undeploy"
    assert args[7] == 3


async def test_suggest_action_requires_action_type():
    db = FakeDb()
    store = make_store(db)
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="conv1")
    result = await handle_suggest_action(store, ctx, {"target_id": 3})
    assert result["status"] == 400
    assert db.executed == []  # 未落库


async def test_suggest_action_store_disabled_returns_error():
    db = FakeDb(enabled=False)
    store = make_store(db)
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="conv1")
    result = await handle_suggest_action(store, ctx, {"action_type": "training_delete"})
    assert result["status"] == 500
