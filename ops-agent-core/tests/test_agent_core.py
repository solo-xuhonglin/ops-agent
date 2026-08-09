import asyncio
import json
import uuid

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
    """模拟 LLMRuntime：select()/bind_tools() 返回自身，按序列返回预设 AIMessage。

    工具轮为带原生 tool_calls 的 AIMessage，结论轮为普通文本（与 graph bind_tools 协议一致）。
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.last_reasoning = None
        self.last_tools = None

    def select(self, reasoning):
        self.last_reasoning = reasoning
        return self

    def bind_tools(self, tools):
        self.last_tools = tools
        return self

    async def astream(self, messages):
        self.calls.append({"messages": list(messages)})
        resp = self.responses.pop(0)
        if isinstance(resp, BaseException):
            raise resp
        yield resp


def make_dispatch(query="how are you", task_id="t-1", token="tok",
                  suggestion_id="", conversation_id="", task_type="chat",
                  reasoning_enabled=True):
    return agent_pb2.ServerMessage(task_dispatch=agent_pb2.TaskDispatch(
        task_id=task_id, query=query, task_token=token, task_type=task_type,
        suggestion_id=suggestion_id, conversation_id=conversation_id,
        reasoning_enabled=reasoning_enabled))


def json_tool_msg(name, args=None):
    """工具调用轮：AIMessage 带原生 tool_calls（bind_tools 协议，content 留空）。"""
    return AIMessage(content="", tool_calls=[{
        "id": f"call_{uuid.uuid4().hex[:8]}",
        "name": name,
        "args": args or {},
    }])


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
    # LLM 被调 2 次，且第二次带上了工具结果回填（原生 ToolMessage，role=tool + tool_call_id）
    assert len(llm.calls) == 2
    types = [m.type for m in llm.calls[1]["messages"]]
    assert "tool" in types  # 原生 tool 角色消息
    tool_msg = next(m for m in llm.calls[1]["messages"] if m.type == "tool")
    assert tool_msg.tool_call_id.startswith("call_")
    # 思考模式透传：默认 reasoning 开启，bind_tools 注入工具列表
    assert llm.last_reasoning is True
    assert llm.last_tools and any(t["function"]["name"] == "training_list" for t in llm.last_tools)


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
        def select(self, reasoning):
            return self

        def bind_tools(self, tools):
            return self

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
    """收敛后结论原样保留；建议不再经 TaskResult 回传（由 approve_* 审批工具落库）。"""
    client = FakeClient()
    registry = ToolRegistry()
    http = FakeHttp()
    llm = FakeLlm([AIMessage(content="一切正常，无需处置。")])

    await core.handle_dispatch(client, registry, llm, http, make_dispatch())

    task_id, ok, conclusion, error = client.results[0]
    assert ok is True
    assert conclusion == "一切正常，无需处置。"
    assert client.suggestion_bodies == []


@pytest.mark.asyncio
async def test_handle_dispatch_reasoning_disabled_selects_fast():
    """前端关掉「深度思考」：LLMRuntime.select(False)（fast 模式）+ 推理链不回传不展示。"""
    client = FakeClient()
    registry = ToolRegistry()
    registry.load([make_tool()])
    http = FakeHttp()
    llm = FakeLlm([AIMessage(content="快速回答")])

    await core.handle_dispatch(client, registry, llm, http,
                               make_dispatch(reasoning_enabled=False))

    assert llm.last_reasoning is False  # fast 模式
    assert client.results[0][2] == "快速回答"
