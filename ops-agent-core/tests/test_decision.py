"""决策轮测试：观察完成后 LLM 决定 plan 下一步（plan_update/suggest_action）。"""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage

from app.agent.context import TaskContext
from app.agent.decision import run_decision_round
from app.agent.graph import handle_plan_create
from app.agent.task_store import TaskStore


class FakeDb:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.plan_row: dict | None = None
        self.sug_rows: list[dict] = []

    @property
    def enabled(self):
        return True

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if "INSERT INTO agent_plans" in sql:
            self.plan_row = {"plan_id": args[0], "conversation_id": args[1],
                             "summary": args[2], "steps": args[3], "status": args[4]}
        elif "UPDATE agent_plans SET steps" in sql and self.plan_row:
            self.plan_row["steps"] = args[1]
        elif "UPDATE agent_plans SET status" in sql and self.plan_row:
            self.plan_row["status"] = args[1]

    async def fetch(self, sql, *args):
        if "agent_suggestions" in sql:
            return self.sug_rows
        return []

    async def fetchrow(self, sql, *args):
        if "FROM agent_plans" in sql and self.plan_row:
            return dict(self.plan_row)
        return None


class FakeLlm:
    """模拟 LLMRuntime：select()/bind_tools() 返回自身；先返回工具调用（原生 tool_calls），再最终文本。"""

    def __init__(self, calls: list[dict], final_text: str = "决策完成"):
        self.calls = calls
        self.final_text = final_text
        self.round = 0

    def select(self, reasoning):
        return self

    def bind_tools(self, tools):
        self._tools = tools
        return self

    async def ainvoke(self, messages):
        if self.round < len(self.calls):
            c = self.calls[self.round]
            self.round += 1
            return AIMessage(content="", tool_calls=[{
                "id": f"call_{self.round}",
                "name": c["name"],
                "args": c["args"],
            }])
        return AIMessage(content=self.final_text)


class FakeHttp:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool, args, ctx):
        self.calls.append((tool.name, args))
        return {"status": 200, "body": json.dumps({"data": {"status": "RUNNING"}})}


class FakeClient:
    def __init__(self):
        self.events: list[tuple[str, str, str]] = []

    async def send_event(self, task_id, kind, content):
        self.events.append((task_id, kind, content))


class FakeRegistry:
    def get(self, name):
        return None

    def all(self):
        return []


class FakeTracker:
    def __init__(self, db):
        self.db = db
        self.notified: list[tuple[str, str, str]] = []

    async def notify_plan(self, plan_id, status, message):
        self.notified.append((plan_id, status, message))


class Monitor:
    def __init__(self, plan_id="", suggestion_id=""):
        self.object_type = "training_job"
        self.object_id = 32
        self.conversation_id = "conv1"
        self.task_id = "t1"
        self.task_token = "tok"
        self.query_tool = "training_get"
        self.query_args = {"jobId": 32}
        self.plan_id = plan_id
        self.suggestion_id = suggestion_id
        self.action_type = "training_create"
        self.target_status = "SUCCEEDED"
        self.check_interval = 10.0
        self.last_checked = 0.0
        self.finished = False
        self.succeeded = False


def make_store(db: FakeDb) -> TaskStore:
    return TaskStore(db, worker_id="w1")


async def _make_plan(db: FakeDb, steps: list[dict]) -> str:
    store = make_store(db)
    await handle_plan_create(store, TaskContext(task_id="t1", task_token="tok",
                                                conversation_id="conv1"),
                             {"summary": "训练并部署", "steps": steps})
    return db.plan_row["plan_id"]


async def test_decision_round_success_advances_to_next_step():
    """观察成功：决策轮先 plan_update 步骤 done，再 suggest_action 下一步。"""
    db = FakeDb()
    plan_id = await _make_plan(db, [
        {"action_type": "training_create", "target_type": "dataset", "target_id": 96},
        {"action_type": "serving_deploy", "target_type": "model_version", "target_id": 0},
    ])
    tracker = FakeTracker(db)
    llm = FakeLlm([
        {"name": "plan_update", "args": {"plan_id": plan_id, "step_no": 1,
                                         "step_status": "done", "note": "训练完成"}},
        {"name": "approve_serving_deploy", "args": {"plan_id": plan_id, "step_no": 2,
                                                    "target_type": "model_version"}},
    ])

    text = await run_decision_round(llm, FakeHttp(), FakeRegistry(), FakeClient(),
                                    make_store(db), tracker,
                                    Monitor(plan_id=plan_id, suggestion_id="sug_1"),
                                    "SUCCEEDED", "job done")

    assert "决策完成" in text
    # step1 标记 done
    upd = [args for sql, args in db.executed if "UPDATE agent_plans SET steps" in sql]
    assert upd
    steps = json.loads(upd[-1][1])
    assert steps[0]["status"] == "done" and steps[0]["note"] == "训练完成"
    # 下一步建议落库（PENDING，带 plan_id/step_no）
    sug = [args for sql, args in db.executed if "INSERT INTO agent_suggestions" in sql]
    assert sug and sug[0][1] == plan_id and sug[0][2] == 2 and sug[0][5] == "serving_deploy"
    # step 通知
    assert tracker.notified and tracker.notified[0][0] == plan_id


async def test_decision_round_success_all_done():
    """观察成功且是最后一步：步骤 done + plan DONE。"""
    db = FakeDb()
    plan_id = await _make_plan(db, [
        {"action_type": "serving_deploy", "target_type": "model_version", "target_id": 0},
    ])
    tracker = FakeTracker(db)
    llm = FakeLlm([
        {"name": "plan_update", "args": {"plan_id": plan_id, "step_no": 1,
                                         "step_status": "done", "note": "部署完成"}},
        {"name": "plan_update", "args": {"plan_id": plan_id, "status": "DONE",
                                         "note": "全部完成"}},
    ])

    await run_decision_round(llm, FakeHttp(), FakeRegistry(), FakeClient(),
                             make_store(db), tracker,
                             Monitor(plan_id=plan_id), "SUCCEEDED", "deployed")

    assert db.plan_row["status"] == "DONE"
    steps = json.loads(db.plan_row["steps"])
    assert steps[0]["status"] == "done"


async def test_decision_round_failure_retry_suggestion():
    """观察失败：步骤标记 failed + suggest_action 重试（retry_of 原建议）。"""
    db = FakeDb()
    plan_id = await _make_plan(db, [
        {"action_type": "training_create", "target_type": "dataset", "target_id": 96},
    ])
    tracker = FakeTracker(db)
    llm = FakeLlm([
        {"name": "plan_update", "args": {"plan_id": plan_id, "step_no": 1,
                                         "step_status": "failed", "note": "显存不足"}},
        {"name": "approve_training_create", "args": {"plan_id": plan_id, "step_no": 1,
                                                     "retry_of": "sug_orig"}},
    ])

    await run_decision_round(llm, FakeHttp(), FakeRegistry(), FakeClient(),
                             make_store(db), tracker,
                             Monitor(plan_id=plan_id, suggestion_id="sug_orig"),
                             "FAILED", "OOM")

    steps = json.loads(db.plan_row["steps"])
    assert steps[0]["status"] == "failed" and steps[0]["note"] == "显存不足"
    sug = [args for sql, args in db.executed if "INSERT INTO agent_suggestions" in sql]
    assert sug and sug[0][11] == "sug_orig"  # retry_of


async def test_decision_round_failure_abandon_plan():
    """观察失败且不可行：步骤 failed + plan CANCELLED。"""
    db = FakeDb()
    plan_id = await _make_plan(db, [
        {"action_type": "serving_deploy", "target_type": "model_version", "target_id": 0},
    ])
    tracker = FakeTracker(db)
    llm = FakeLlm([
        {"name": "plan_update", "args": {"plan_id": plan_id, "step_no": 1,
                                         "step_status": "failed", "note": "无可用 GPU"}},
        {"name": "plan_update", "args": {"plan_id": plan_id, "status": "CANCELLED",
                                         "note": "方案不可行"}},
    ])

    await run_decision_round(llm, FakeHttp(), FakeRegistry(), FakeClient(),
                             make_store(db), tracker,
                             Monitor(plan_id=plan_id), "FAILED", "no gpu")

    assert db.plan_row["status"] == "CANCELLED"


async def test_decision_round_write_tool_rejected():
    """决策轮严禁直接执行写工具（403）。"""
    db = FakeDb()
    store = make_store(db)
    llm = FakeLlm([{"name": "training_create", "args": {"jobId": 1}}])
    text = await run_decision_round(llm, FakeHttp(), FakeRegistry(), FakeClient(),
                                    store, FakeTracker(db), Monitor(), "FAILED")
    assert "决策完成" in text  # 工具被拒后模型继续输出


async def test_decision_round_no_plan_ok():
    """无关联 plan 时决策轮也能安全结束。"""
    db = FakeDb()
    llm = FakeLlm([], final_text="无计划，仅汇报")
    text = await run_decision_round(llm, FakeHttp(), FakeRegistry(), FakeClient(),
                                    make_store(db), FakeTracker(db), Monitor(), "SUCCEEDED")
    assert "无计划" in text
