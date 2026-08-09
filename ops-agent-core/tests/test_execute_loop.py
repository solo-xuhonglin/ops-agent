"""execute 任务内闭环测试：系统直调写工具 → 同一任务决策图 → agent 自主推进 → 收敛。

取代旧「写工具 → LLM 总结秒回」与 continue 推进；写工具安全边界不变（不进 tools）。
"""
import json

import pytest
from langchain_core.messages import AIMessage

from app.agent import core
from app.agent.context import TaskContext
from app.transport import agent_pb2
from tests.test_agent_core import FakeClient, FakeLlm, json_tool_msg


class FakeStore:
    """execute 收尾用 store mock：suggestion/plan 查询 + task/suggestion 状态写。"""

    def __init__(self, suggestion=None, plan=None):
        self.enabled = True
        self.suggestion = suggestion or {
            "suggestion_id": "sug-1", "plan_id": "plan-1", "step_no": 1,
            "action_type": "training_create", "target_type": "dataset", "target_id": 96}
        self.plan = plan or {"plan_id": "plan-1", "summary": "训练并部署", "status": "RUNNING",
                             "steps": [{"step_no": 1, "action_type": "training_create",
                                        "status": "pending"}]}
        self.task_inserts = []
        self.task_finishes = []
        self.suggestion_updates = []

    async def insert_task(self, task_id, task_type, conversation_id, query="", suggestion_id=""):
        self.task_inserts.append((task_id, task_type, conversation_id, suggestion_id))

    async def finish_task(self, task_id, status, conclusion=""):
        self.task_finishes.append((task_id, status, conclusion))

    async def update_suggestion_result(self, suggestion_id, status, result_text=""):
        self.suggestion_updates.append((suggestion_id, status))

    async def get_suggestion(self, suggestion_id):
        return self.suggestion

    async def get_plan(self, plan_id):
        return self.plan


class FakeTracker:
    def __init__(self):
        self.registered = []

    def register(self, **kwargs):
        self.registered.append(kwargs)


class SeqHttp:
    """可编程响应序列：写工具调用返回第一个响应，后续查询返回其余响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call(self, tool, args, ctx):
        self.calls.append((tool.name, dict(args)))
        resp = self.responses.pop(0) if self.responses else self.responses[-1]
        return resp


def write_tool(name="training_create", method="POST", path="/api/training/jobs"):
    return agent_pb2.ToolSchema(name=name, description="d",
                                parameters='{"type":"object","properties":{}}',
                                is_write=True, http_method=method, path_template=path)


def read_tool(name="training_get", path="/api/training/jobs/{jobId}"):
    return agent_pb2.ToolSchema(name=name, description="d",
                                parameters='{"type":"object","properties":{}}',
                                is_write=False, http_method="GET", path_template=path)


def make_execute(action_type="training_create", suggestion_id="sug-1", params=None):
    return agent_pb2.TaskDispatch(
        task_id="exec-1", task_token="tok", task_type="execute",
        conversation_id="c-1", suggestion_id=suggestion_id,
        action_type=action_type,
        params=json.dumps(params or {"datasetId": 96, "name": "lstm"}),
        target_type="dataset", target_id=96, grant_key="gkey")


def make_ctx(d):
    return TaskContext(task_id=d.task_id, task_token=d.task_token,
                       conversation_id=d.conversation_id, suggestion_id=d.suggestion_id,
                       grant_key=d.grant_key)


def make_registry(tools):
    from app.tools.registry import ToolRegistry
    reg = ToolRegistry()
    reg.load(tools)
    return reg


def body(payload, status=200):
    return {"status": status, "body": json.dumps(payload, ensure_ascii=False)}


@pytest.mark.asyncio
async def test_execute_loop_converges_after_write():
    """写工具成功 → 图内一轮收敛：结论=assistant 文本，suggestion 置 EXECUTED，task 落库。"""
    client = FakeClient()
    reg = make_registry([write_tool(), read_tool()])
    http = SeqHttp([body({"data": {"id": 32, "status": "PENDING"}})])
    llm = FakeLlm([AIMessage(content="训练任务 32 已提交，正在等待完成。")])
    store = FakeStore()
    d = make_execute()

    await core.handle_execute(client, reg, llm, http, make_ctx(d), d, store)

    # 写工具被系统直调一次（图内未重复执行）
    assert http.calls[0][0] == "training_create"
    assert len(http.calls) == 1
    # 收敛结论 = 图内最终 assistant 内容
    task_id, ok, conclusion, _ = client.results[0]
    assert ok is True
    assert conclusion == "训练任务 32 已提交，正在等待完成。"
    # suggestion 状态 + task 落库保留
    assert ("sug-1", "EXECUTED") in store.suggestion_updates
    assert any(t[1] == "execute" and t[3] == "sug-1" for t in store.task_inserts)


@pytest.mark.asyncio
async def test_execute_loop_wait_until_advance():
    """图内 agent 用 wait_until 轮询：写工具成功 → wait_until 查到 SUCCEEDED → 收敛。"""
    client = FakeClient()
    reg = make_registry([write_tool(), read_tool()])
    http = SeqHttp([
        body({"data": {"id": 32, "status": "PENDING"}}),      # 写工具结果
        body({"data": {"id": 32, "status": "SUCCEEDED", "updated_at": "10"}}),  # wait_until 查询
    ])
    llm = FakeLlm([
        json_tool_msg("wait_until", {"query_tool": "training_get", "object_id": 32,
                                     "wait_seconds": 30, "target_status": "SUCCEEDED"}),
        AIMessage(content="训练任务 32 已完成。"),
    ])
    store = FakeStore()
    d = make_execute()

    await core.handle_execute(client, reg, llm, http, make_ctx(d), d, store)

    # 图内调了一次 wait_until（底层走 training_get）
    assert ("training_get", {"jobId": 32}) in http.calls
    assert client.results[0][1] is True
    assert client.results[0][2] == "训练任务 32 已完成。"
    # wait_until 的 progress 事件已发出
    assert any(e[1] == "progress" for e in client.events)


@pytest.mark.asyncio
async def test_execute_loop_write_failed_in_graph():
    """写工具失败 → 图内 agent 看失败原因决策 → 收敛，suggestion 置 FAILED，send_result ok=False。"""
    client = FakeClient()
    reg = make_registry([write_tool(), read_tool()])
    http = SeqHttp([body({"code": 400, "msg": "数据集不存在"}, status=400)])
    llm = FakeLlm([AIMessage(content="执行失败：数据集 96 不存在，已建议修正参数。")])
    store = FakeStore()
    d = make_execute()

    await core.handle_execute(client, reg, llm, http, make_ctx(d), d, store)

    task_id, ok, conclusion, _ = client.results[0]
    assert ok is False
    assert "执行失败" in conclusion
    assert ("sug-1", "FAILED") in store.suggestion_updates


@pytest.mark.asyncio
async def test_execute_loop_registers_monitor():
    """写工具成功 → Monitor 仍注册（对象类型/ID/查询工具映射），兜底长等待。"""
    client = FakeClient()
    reg = make_registry([write_tool(), read_tool()])
    http = SeqHttp([body({"data": {"id": 32, "status": "PENDING"}})])
    llm = FakeLlm([AIMessage(content="已提交训练任务 32。")])
    store = FakeStore()
    tracker = FakeTracker()
    d = make_execute()

    await core.handle_execute(client, reg, llm, http, make_ctx(d), d, store, tracker)

    assert len(tracker.registered) == 1
    m = tracker.registered[0]
    assert m["object_type"] == "training_job"
    assert m["object_id"] == 32
    assert m["query_tool"] == "training_get"
    assert m["target_status"] == "SUCCEEDED"
