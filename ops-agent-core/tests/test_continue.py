"""continue 任务测试：admin 在 execute 成功后自动派发，worker 复用决策轮推进 plan。"""
import asyncio

import pytest

from app.agent import core
from app.agent.context import TaskContext
from app.transport import agent_pb2


class FakeLlm:
    """LLMRuntime mock：select() 返回可 bind_tools 的链，ainvoke 返回预设 tool_calls。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.round = 0
        self.last_tools = None

    def select(self, reasoning=True):
        return _SelectedLlm(self, reasoning)


class _SelectedLlm:
    def __init__(self, parent, reasoning):
        self.parent = parent

    def bind_tools(self, tools):
        self.parent.last_tools = tools
        return self

    async def ainvoke(self, messages):
        if self.parent.round >= len(self.parent.responses):
            return _ToolResult(content="决策完成", tool_calls=[])
        item = self.parent.responses[self.parent.round]
        self.parent.round += 1
        if isinstance(item, BaseException):
            raise item
        return _ToolResult(content=item.get("content", ""),
                           tool_calls=item.get("tool_calls", []))


class _ToolResult:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeClient:
    def __init__(self):
        self.events = []
        self.results = []

    async def send_event(self, task_id, event_type, content):
        self.events.append((task_id, event_type, content))

    async def send_result(self, task_id, ok, conclusion, error="", suggestions=None, reasoning=""):
        self.results.append({"task_id": task_id, "ok": ok, "conclusion": conclusion, "error": error})


class FakeStore:
    """TaskStore mock：plan/suggestion 落库，get_plan/get_suggestion 返回预设。"""

    def __init__(self):
        self.enabled = True
        self.plans = {"plan-1": {
            "plan_id": "plan-1", "summary": "采集并训练",
            "steps": [{"step_no": 1, "action_type": "dataset_collect", "status": "done"},
                      {"step_no": 2, "action_type": "training_create", "status": "planned"}]}}
        self.suggestions = {
            "sug-1": {"suggestion_id": "sug-1", "plan_id": "plan-1", "step_no": 1,
                      "action_type": "dataset_collect", "target_type": "dataset", "target_id": 3},
            "sug-orphan": {"suggestion_id": "sug-orphan", "plan_id": "", "step_no": 0,
                           "action_type": "dataset_collect", "target_type": "dataset", "target_id": 3},
        }
        self.suggestion_inserts = []
        self.task_inserts = []
        self.task_finishes = []

    async def get_plan(self, plan_id):
        return self.plans.get(plan_id)

    async def get_suggestion(self, suggestion_id):
        return self.suggestions.get(suggestion_id)

    async def insert_suggestion(self, s):
        sid = "sug-new-" + str(len(self.suggestion_inserts))
        new = dict(s, suggestion_id=sid)
        self.suggestion_inserts.append(new)
        return sid

    async def insert_task(self, task_id, task_type, conversation_id, query=""):
        self.task_inserts.append({"task_id": task_id, "task_type": task_type})

    async def finish_task(self, task_id, status, conclusion=""):
        self.task_finishes.append({"task_id": task_id, "status": status, "conclusion": conclusion})

    async def update_plan_status(self, plan_id, status):
        return None

    async def update_plan_step_status(self, plan_id, step_no, status, note=""):
        return None


class FakeRegistry:
    def all(self):
        return []

    def get(self, name):
        return None

    def load(self, schemas):
        pass


def make_dispatch(task_id="cont-1", suggestion_id="sug-1", query="step 1 done"):
    return agent_pb2.ServerMessage(task_dispatch=agent_pb2.TaskDispatch(
        task_id=task_id, task_token="tok", task_type="continue",
        conversation_id="c-1", suggestion_id=suggestion_id, query=query))


def make_ctx(dispatch):
    d = dispatch.task_dispatch
    return TaskContext(task_id=d.task_id, task_token=d.task_token,
                       conversation_id=d.conversation_id, suggestion_id=d.suggestion_id)


@pytest.mark.asyncio
async def test_continue_skips_when_no_plan():
    """suggestion 没有 plan_id → 跳过决策轮，直接 send_result 提示无关联计划。"""
    client = FakeClient()
    store = FakeStore()
    llm = FakeLlm([])
    d = make_dispatch(suggestion_id="sug-orphan")
    ctx = make_ctx(d)

    await core.handle_continue(client, FakeRegistry(), llm, None, ctx, d, store, None)

    assert llm.round == 0  # 没调决策轮
    assert client.results[0]["ok"] is True
    assert "无关联计划" in client.results[0]["conclusion"]
    assert store.suggestion_inserts == []  # 不会落新建议


@pytest.mark.asyncio
async def test_continue_invokes_decision_with_plan():
    """有 plan 时调决策轮，模型提下一步 approve_* 落新建议。"""
    client = FakeClient()
    store = FakeStore()
    # 决策轮：先调 approve_training_create 落新建议，再收敛结论
    llm = FakeLlm([
        {"tool_calls": [{"id": "c1", "name": "approve_training_create",
                         "args": {"datasetId": 3, "name": "lstm", "plan_id": "plan-1", "step_no": 2}}]},
    ])
    msg = make_dispatch()
    d = msg.task_dispatch
    ctx = make_ctx(msg)

    await core.handle_continue(client, FakeRegistry(), llm, None, ctx, d, store, None)

    # 任务行已落库
    assert any(t["task_type"] == "continue" for t in store.task_inserts)
    # 模型决定落了一条新建议
    assert len(store.suggestion_inserts) >= 1
    # send_result ok
    assert client.results[-1]["ok"] is True
    # finish_task 收尾
    assert any(f["status"] == "SUCCEEDED" for f in store.task_finishes)


@pytest.mark.asyncio
async def test_continue_handles_db_failure():
    """store.get_suggestion 异常 → send_result ok=False，不阻塞。"""
    class BrokenStore(FakeStore):
        async def get_suggestion(self, suggestion_id):
            raise RuntimeError("db down")
    client = FakeClient()
    store = BrokenStore()
    llm = FakeLlm([])
    msg = make_dispatch()
    d = msg.task_dispatch
    ctx = make_ctx(msg)

    await core.handle_continue(client, FakeRegistry(), llm, None, ctx, d, store, None)

    assert client.results[-1]["ok"] is False
    assert "db down" in client.results[-1]["conclusion"]