"""wait_until 内置工具测试：提前返回（target/终态/updated_at 变化）、超时返回、查询失败兜底。"""
import json

import pytest

import app.agent.graph as graph_mod
from app.agent.context import TaskContext
from app.agent.graph import handle_wait_until


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.path_template = "/api/x"
        self.http_method = "GET"
        self.is_write = False
        self.parameters = "{}"


class FakeRegistry:
    def __init__(self, tools):
        self.tools = {t.name: t for t in tools}

    def get(self, name):
        return self.tools.get(name)


class FakeHttp:
    """可编程响应序列：每次 call 消费下一个响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call(self, tool, args, ctx):
        self.calls.append((tool.name, dict(args)))
        resp = self.responses.pop(0) if self.responses else []
        return resp


class FakeClient:
    def __init__(self):
        self.events = []

    async def send_event(self, task_id, event_type, content):
        self.events.append((task_id, event_type, content))


def body(status, updated_at=None, **extra):
    data = {"id": 32, "status": status}
    if updated_at:
        data["updated_at"] = updated_at
    data.update(extra)
    return {"status": 200, "body": json.dumps({"code": 0, "data": data})}


def ctx(task_id="t-1"):
    return TaskContext(task_id=task_id, task_token="tok")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """测试不真等：轮询间隔置 0。"""
    monkeypatch.setattr(graph_mod, "WAIT_POLL_INTERVAL_S", 0.0)


@pytest.mark.asyncio
async def test_wait_until_returns_on_target_status():
    """查询命中 target_status → 只查一次立即返回。"""
    http = FakeHttp([body("SUCCEEDED", updated_at="10")])
    client = FakeClient()
    reg = FakeRegistry([FakeTool("training_get")])
    result = await handle_wait_until(reg, http, client, ctx(), {
        "query_tool": "training_get", "object_id": 32, "wait_seconds": 30,
        "target_status": "SUCCEEDED"})
    assert len(http.calls) == 1
    assert http.calls[0] == ("training_get", {"jobId": 32})
    payload = json.loads(result["body"])
    assert payload["status"] == "SUCCEEDED"
    assert payload["_still_in_progress"] is False


@pytest.mark.asyncio
async def test_wait_until_returns_on_terminal_failure():
    """查询返回终态 FAILED（非 target）→ 立即返回让 agent 看到失败。"""
    http = FakeHttp([body("FAILED", updated_at="10")])
    client = FakeClient()
    reg = FakeRegistry([FakeTool("training_get")])
    result = await handle_wait_until(reg, http, client, ctx(), {
        "query_tool": "training_get", "object_id": 32, "wait_seconds": 30,
        "target_status": "SUCCEEDED"})
    assert len(http.calls) == 1
    payload = json.loads(result["body"])
    assert payload["status"] == "FAILED"
    assert payload["_still_in_progress"] is False


@pytest.mark.asyncio
async def test_wait_until_returns_on_updated_at_change():
    """updated_at 变化 → 提前返回最新数据。"""
    http = FakeHttp([
        body("RUNNING", updated_at="10"),
        body("RUNNING", updated_at="12"),
    ])
    client = FakeClient()
    reg = FakeRegistry([FakeTool("training_get")])
    result = await handle_wait_until(reg, http, client, ctx(), {
        "query_tool": "training_get", "object_id": 32, "wait_seconds": 30})
    assert len(http.calls) == 2
    payload = json.loads(result["body"])
    assert payload["updated_at"] == "12"
    assert payload["_still_in_progress"] is False


@pytest.mark.asyncio
async def test_wait_until_timeout_returns_still_in_progress():
    """wait_seconds=0 → 第一轮无变化即超时返回 still_in_progress。"""
    http = FakeHttp([body("RUNNING", updated_at="10")])
    client = FakeClient()
    reg = FakeRegistry([FakeTool("training_get")])
    result = await handle_wait_until(reg, http, client, ctx(), {
        "query_tool": "training_get", "object_id": 32, "wait_seconds": 0})
    assert len(http.calls) == 1
    payload = json.loads(result["body"])
    assert payload["status"] == "RUNNING"
    assert payload["_still_in_progress"] is True


@pytest.mark.asyncio
async def test_wait_until_sends_progress_events():
    """等待期间发 progress 事件（含当前状态）。"""
    http = FakeHttp([
        body("RUNNING", updated_at="10"),
        body("SUCCEEDED", updated_at="10"),
    ])
    client = FakeClient()
    reg = FakeRegistry([FakeTool("training_get")])
    await handle_wait_until(reg, http, client, ctx(), {
        "query_tool": "training_get", "object_id": 32, "wait_seconds": 30,
        "target_status": "SUCCEEDED"})
    progress = [e for e in client.events if e[1] == "progress"]
    assert progress
    assert "SUCCEEDED" in progress[-1][2]


@pytest.mark.asyncio
async def test_wait_until_unknown_tool():
    """query_tool 不存在 → 直接返回错误，不查询。"""
    http = FakeHttp([])
    client = FakeClient()
    reg = FakeRegistry([])
    result = await handle_wait_until(reg, http, client, ctx(), {
        "query_tool": "nope", "object_id": 1, "wait_seconds": 30})
    assert http.calls == []
    assert "unknown" in result["body"]
