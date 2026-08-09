import asyncio
import json

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

    async def send_result(self, task_id, ok, conclusion, error="", suggestions=None, reasoning=""):
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
    """按序列返回预设 AIMessage；工具轮为 JSON 文本（契约解析），结论轮为普通文本。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def astream(self, messages):
        self.calls.append({"messages": list(messages)})
        resp = self.responses.pop(0)
        if isinstance(resp, BaseException):
            raise resp
        yield resp


def make_dispatch(query="how are you", task_id="t-1", token="tok",
                  suggestion_id="", conversation_id="", task_type="chat"):
    return agent_pb2.ServerMessage(task_dispatch=agent_pb2.TaskDispatch(
        task_id=task_id, query=query, task_token=token, task_type=task_type,
        suggestion_id=suggestion_id, conversation_id=conversation_id))


def json_tool_msg(name, args=None):
    """工具调用轮：AIMessage 输出 JSON 契约文本（由 _parse_tool_calls 解析）。"""
    return AIMessage(content=json.dumps({"tool": name, "args": args or {}}, ensure_ascii=False))


def make_tool(name="training_list", method="GET", path="/api/training/jobs",
              params='{"type":"object","properties":{},"required":[]}'):
    return agent_pb2.ToolSchema(name=name, description="d", parameters=params,
                                is_write=False, http_method=method, path_template=path)


class CancelLlm(FakeLlm):
    """LLM 调用点抛 CancelledError（模拟 admin CancelTask → task.cancel()）。"""

    async def astream(self, messages):
        raise asyncio.CancelledError
        yield  # pragma: no cover - 使 astream 成为 async generator


@pytest.mark.asyncio
async def test_handle_dispatch_cancelled_no_result():
    """admin 取消：异常传播（LangGraph 包装为 NodeCancelledError）且不回发 result（避免覆盖 admin 侧 CANCELLED）。"""
    from langgraph.errors import NodeCancelledError

    client = FakeClient()
    registry = ToolRegistry()
    llm = CancelLlm([])
    http = FakeHttp()
    with pytest.raises(NodeCancelledError):
        await core.handle_dispatch(client, registry, llm, http, make_dispatch())
    assert client.results == []  # 未发 TaskResult


@pytest.mark.asyncio
async def test_handle_dispatch_loops_until_converged():
    client = FakeClient()
    registry = ToolRegistry()
    registry.load([make_tool()])
    http = FakeHttp()
    llm = FakeLlm([json_tool_msg("training_list", {"page": 0}),
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
    # LLM 被调 2 次，且第二次带上了工具结果回填（普通 system 消息，非 tool 角色）
    assert len(llm.calls) == 2
    types = [m.type for m in llm.calls[1]["messages"]]
    assert "system" in types
    assert "tool" not in types  # 不使用 tool 角色消息（reasoner 兼容）


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
    llm = FakeLlm([json_tool_msg("training_get"), AIMessage(content="done")])

    await core.handle_dispatch(client, registry, llm, http, make_dispatch())

    assert http.calls == []
    assert client.results[0][1] is True
    assert client.results[0][2] == "done"


@pytest.mark.asyncio
async def test_handle_dispatch_llm_error_marks_failed():
    client = FakeClient()

    class BoomLlm:
        async def astream(self, messages):
            raise RuntimeError("llm down")
            yield  # pragma: no cover - 使 astream 成为 async generator

    registry = ToolRegistry()
    await core.handle_dispatch(client, registry, BoomLlm(), FakeHttp(), make_dispatch())

    task_id, ok, conclusion, error = client.results[0]
    assert ok is False
    assert "llm down" in error


def test_parse_suggestions_removed():
    """写操作建议已工具化（suggest_action/plan_create），core 不再解析 JSON 建议块。"""
    assert not hasattr(core, "_parse_suggestions")
    assert not hasattr(core, "_persist_outputs")


@pytest.mark.asyncio
async def test_handle_dispatch_conclusion_preserved():
    """收敛后结论原样保留；建议不再经 TaskResult 回传（由 suggest_action 工具落库）。"""
    client = FakeClient()
    registry = ToolRegistry()
    http = FakeHttp()
    llm = FakeLlm([AIMessage(content="一切正常，无需处置。")])

    await core.handle_dispatch(client, registry, llm, http, make_dispatch())

    task_id, ok, conclusion, error = client.results[0]
    assert ok is True
    assert conclusion == "一切正常，无需处置。"
    assert client.suggestion_bodies == []
