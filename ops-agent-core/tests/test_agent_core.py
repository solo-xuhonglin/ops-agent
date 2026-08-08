import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent import core
from app.tools.registry import ToolRegistry
from app.transport import agent_pb2


class FakeClient:
    def __init__(self):
        self.events = []
        self.results = []
        self.suggestion_bodies = []

    async def send_event(self, task_id, event_type, content):
        self.events.append((task_id, event_type, content))

    async def send_result(self, task_id, ok, conclusion, error="", suggestions=None):
        self.results.append((task_id, ok, conclusion, error))
        if suggestions:
            self.suggestion_bodies.extend(suggestions)


class FakeHttp:
    def __init__(self, body='{"items": []}'):
        self.calls = []
        self.body = body

    async def call(self, tool, args, ctx):
        self.calls.append((tool.name, args, ctx))
        return {"status": 200, "body": self.body}


class FakeLlm:
    """按序列返回预设 AIMessage：assistant(tool_call) → assistant(conclusion)。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def bind_tools(self, tools):
        self.last_tools = tools
        return self

    async def ainvoke(self, messages):
        self.calls.append({"messages": list(messages), "tools": getattr(self, "last_tools", None)})
        return self.responses.pop(0)


def make_dispatch(task_type="question", query="how are you", task_id="t-1", token="tok"):
    return agent_pb2.ServerMessage(task_dispatch=agent_pb2.TaskDispatch(
        task_id=task_id, task_type=task_type, query=query, task_token=token))


def tool_call_msg(name, call_id="c1", args="{}"):
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": _parse_args(args), "id": call_id, "type": "tool_call"}])


def _parse_args(args: str) -> dict:
    import json
    try:
        return json.loads(args or "{}")
    except json.JSONDecodeError:
        return {}


def make_tool(name="training_list", method="GET", path="/api/training/jobs",
              params='{"type":"object","properties":{},"required":[]}'):
    return agent_pb2.ToolSchema(name=name, description="d", parameters=params,
                                is_write=False, http_method=method, path_template=path)


@pytest.mark.asyncio
async def test_handle_dispatch_loops_until_converged():
    client = FakeClient()
    registry = ToolRegistry()
    registry.load([make_tool()])
    http = FakeHttp()
    llm = FakeLlm([tool_call_msg("training_list", args='{"page":0}'),
                   AIMessage(content="系统状态正常")])

    await core.handle_dispatch(client, registry, llm, http, make_dispatch())

    # 事件：1 个 progress + 1 个 tool_call；工具被调用 1 次
    assert client.events[0][1] == "progress"
    assert client.events[1][1] == "tool_call"
    assert http.calls[0][0] == "training_list"
    # 结果 ok + 结论来自 LLM
    task_id, ok, conclusion, _ = client.results[0]
    assert ok is True
    assert conclusion == "系统状态正常"
    # LLM 被调 2 次，且第二次带上了 tool 回填
    assert len(llm.calls) == 2
    types = [m.type for m in llm.calls[1]["messages"]]
    assert "tool" in types


@pytest.mark.asyncio
async def test_handle_dispatch_no_tool_calls_returns_direct_answer():
    client = FakeClient()
    registry = ToolRegistry()
    http = FakeHttp()
    llm = FakeLlm([AIMessage(content="你好，我是运维助手")])

    await core.handle_dispatch(client, registry, llm, http, make_dispatch())

    assert len(client.results) == 1
    assert client.results[0][1] is True
    assert client.results[0][2] == "你好，我是运维助手"
    assert http.calls == []  # 未调任何工具


@pytest.mark.asyncio
async def test_handle_dispatch_unknown_tool_not_crashed():
    client = FakeClient()
    registry = ToolRegistry()  # 空注册表
    http = FakeHttp()
    llm = FakeLlm([tool_call_msg("training_get"), AIMessage(content="done")])

    await core.handle_dispatch(client, registry, llm, http, make_dispatch())

    assert http.calls == []
    assert client.results[0][1] is True
    assert client.results[0][2] == "done"


@pytest.mark.asyncio
async def test_handle_dispatch_llm_error_marks_failed():
    client = FakeClient()

    class BoomLlm:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            raise RuntimeError("llm down")

    registry = ToolRegistry()
    await core.handle_dispatch(client, registry, BoomLlm(), FakeHttp(), make_dispatch())

    task_id, ok, conclusion, error = client.results[0]
    assert ok is False
    assert "llm down" in error


def test_parse_suggestions_extracts_json_block():
    content = ("发现 serving 异常。```json {\"suggestions\":[{\"action_type\":\"serving_undeploy\","
               "\"target_type\":\"serving_endpoint\",\"target_id\":3,\"reason\":\"不健康\","
               "\"priority\":\"HIGH\"}]} ```")
    items = core._parse_suggestions(content)
    assert len(items) == 1
    assert items[0]["action_type"] == "serving_undeploy"
    assert items[0]["target_id"] == 3


def test_parse_suggestions_empty_without_block():
    assert core._parse_suggestions("一切正常，无需处置。") == []
    assert core._parse_suggestions(None) == []
    assert core._parse_suggestions("```json {bad json} ```") == []


@pytest.mark.asyncio
async def test_handle_dispatch_sends_suggestions():
    client = FakeClient()
    registry = ToolRegistry()
    http = FakeHttp()
    content = ("建议下线。```json {\"suggestions\":[{\"action_type\":\"serving_undeploy\","
               "\"target_type\":\"serving_endpoint\",\"target_id\":3}]} ```")
    llm = FakeLlm([AIMessage(content=content)])

    await core.handle_dispatch(client, registry, llm, http, make_dispatch())

    task_id, ok, conclusion, error = client.results[0]
    assert ok is True
    assert "```json" not in conclusion  # 建议块已剥离
    assert client.suggestion_bodies and len(client.suggestion_bodies) == 1
    assert client.suggestion_bodies[0].action_type == "serving_undeploy"
    assert client.suggestion_bodies[0].target_id == 3
