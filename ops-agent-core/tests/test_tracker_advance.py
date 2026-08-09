"""推进轮测试：Monitor 到终态 → suggestion 机械更新 + worker 自治决策图推进（不经 admin）。"""
import pytest
from langchain_core.messages import AIMessage

from app.agent.tracker import Monitor, TaskTracker
from tests.test_agent_core import FakeClient, FakeLlm


class FakeStore:
    def __init__(self, plan=None):
        self.enabled = True
        self.plan = plan or {"plan_id": "plan-1", "summary": "训练并部署",
                             "status": "RUNNING", "conversation_id": "c-1",
                             "steps": [{"step_no": 1, "action_type": "training_create",
                                        "status": "pending"}]}
        self.suggestion_updates = []
        self.task_inserts = []
        self.task_finishes = []
        self.plan_status_updates = []

    async def update_suggestion_result(self, suggestion_id, status, result_text=""):
        self.suggestion_updates.append((suggestion_id, status))

    async def get_plan(self, plan_id):
        return self.plan

    async def insert_task(self, task_id, task_type, conversation_id, query="", suggestion_id=""):
        self.task_inserts.append((task_id, task_type, conversation_id, suggestion_id))

    async def finish_task(self, task_id, status, conclusion=""):
        self.task_finishes.append((task_id, status, conclusion))

    async def update_plan_status(self, plan_id, status):
        self.plan_status_updates.append((plan_id, status))

    async def pending_steps(self, plan_id):
        return []


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def call(self, tool, args, ctx):
        self.calls.append((tool.name, dict(args)))
        return {"status": 200, "body": '{"data": {"status": "SUCCEEDED"}}'}


class FakeRegistry:
    def get(self, name):
        return None

    def all(self):
        return []


def make_monitor():
    return Monitor(
        object_type="training_job", object_id=32, conversation_id="c-1",
        task_id="exec-1", task_token="tok", query_tool="training_get",
        query_args={"jobId": 32}, plan_id="plan-1", suggestion_id="sug-1",
        action_type="training_create", target_status="SUCCEEDED")


@pytest.mark.asyncio
async def test_on_done_triggers_advance_round():
    """终态达成 → suggestion 机械 EXECUTED + 推进轮（plan_advance 事件 + 决策图收敛回传）。"""
    client = FakeClient()
    store = FakeStore()
    llm = FakeLlm([AIMessage(content="步骤 1 已完成，下一步建议部署服务。")])
    tracker = TaskTracker(store, FakeHttp(), client, FakeRegistry(), llm=llm)
    monitor = make_monitor()

    await tracker._on_done(monitor, observation="training done")

    assert ("sug-1", "EXECUTED") in store.suggestion_updates
    # plan_advance 事件：task_id=plan_advance:plan-1，content 带 conversationId
    advance_events = [e for e in client.events if e[1] == "plan_advance"]
    assert advance_events
    assert advance_events[0][0] == "plan_advance:plan-1"
    # 决策图收敛后 send_result（task_id 同推进轮）
    assert client.results and client.results[-1][0] == "plan_advance:plan-1"
    assert client.results[-1][2] == "步骤 1 已完成，下一步建议部署服务。"
    # task 落库 advance 类型
    assert any(t[1] == "advance" for t in store.task_inserts)


@pytest.mark.asyncio
async def test_on_failed_triggers_advance_round():
    """终态失败 → suggestion 机械 FAILED + 推进轮。"""
    client = FakeClient()
    store = FakeStore()
    llm = FakeLlm([AIMessage(content="步骤失败，建议修正参数重试。")])
    tracker = TaskTracker(store, FakeHttp(), client, FakeRegistry(), llm=llm)
    monitor = make_monitor()

    await tracker._on_failed(monitor, "FAILED", observation="train failed")

    assert ("sug-1", "FAILED") in store.suggestion_updates
    assert any(e[1] == "plan_advance" for e in client.events)
    assert client.results[-1][0] == "plan_advance:plan-1"


@pytest.mark.asyncio
async def test_on_done_without_llm_mechanical_fallback():
    """无 LLM：不跑推进轮，机械 plan_update 通知降级（发 plan_update 事件）。"""
    client = FakeClient()
    store = FakeStore()
    tracker = TaskTracker(store, FakeHttp(), client, FakeRegistry(), llm=None)
    monitor = make_monitor()

    await tracker._on_done(monitor, observation="done")

    assert not any(e[1] == "plan_advance" for e in client.events)
    assert any(e[1] == "plan_update" for e in client.events)
