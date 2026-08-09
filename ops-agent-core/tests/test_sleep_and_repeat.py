"""sleep 内置工具 + 只读工具重复调用检测。"""
import json

import pytest

import app.agent.graph as graph_mod
from app.agent.context import TaskContext
from app.agent.graph import build_graph, handle_sleep, run_graph
from app.tools.registry import ToolRegistry
from tests.test_agent_core import FakeClient, FakeHttp, make_tool, json_tool_msg


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(graph_mod, "WAIT_POLL_INTERVAL_S", 0.0)


@pytest.mark.asyncio
async def test_sleep_returns_after_seconds():
    """sleep N 秒后返回 ok，body 含 slept_seconds + message。"""
    client = FakeClient()
    ctx = TaskContext(task_id="t", task_token="tok")
    result = await handle_sleep(client, ctx, {"seconds": 3})
    assert result["status"] == 200
    body = json.loads(result["body"])
    assert body["slept_seconds"] == 3
    # progress 事件已发
    assert any(e[1] == "progress" and "sleep 3s" in e[2] for e in client.events)


@pytest.mark.asyncio
async def test_sleep_clamps_to_300(monkeypatch):
    """seconds=999 clamp 到 300（避免长挂）；不真等（monkeypatch asyncio.sleep）。"""
    import asyncio as _asyncio
    waited = []

    async def fake_sleep(secs):
        waited.append(secs)

    monkeypatch.setattr(_asyncio, "sleep", fake_sleep)
    client = FakeClient()
    result = await handle_sleep(client, TaskContext(task_id="t", task_token="tok"),
                                {"seconds": 999})
    body = json.loads(result["body"])
    assert body["slept_seconds"] == 300
    assert waited == [300]


@pytest.mark.asyncio
async def test_repeat_read_blocked_with_hint():
    """连续两次相同 training_get → 第二次返回 400 + wait_until 提示，不再调 http。"""
    client = FakeClient()
    http = FakeHttp(body='{"data": {"status": "RUNNING"}}')
    from langchain_core.messages import AIMessage

    class TwiceLlm:
        def __init__(self):
            self.round = 0
        def select(self, r): return self
        def bind_tools(self, t): return self
        async def astream(self, m):
            self.round += 1
            if self.round == 1:
                yield json_tool_msg("training_get", {"jobId": 10})
            elif self.round == 2:
                # 模型无视 400 又调了一次同样的 training_get
                yield json_tool_msg("training_get", {"jobId": 10})
            else:
                yield AIMessage(content="完成")

    registry = ToolRegistry()
    registry.load([make_tool("training_get", path="/api/training/jobs/{jobId}")])
    graph = build_graph(llm_runtime=TwiceLlm(), http=http, registry=registry, client=client)

    final, _ = await run_graph(graph, TaskContext(task_id="t", task_token="tok"), [], max_rounds=10)

    # http 只被调了一次（第二次被拒绝）
    assert len(http.calls) == 1
    # tool_result 提示含 wait_until 指引
    tool_msgs = [m for m in final if m.type == "tool"]
    assert any("wait_until" in m.content and "training_get" in m.content for m in tool_msgs)


@pytest.mark.asyncio
async def test_repeat_guard_does_not_block_writes():
    """写工具（is_write=True）不参与重复检测（即使两次相同参数也能继续）。"""
    client = FakeClient()
    http = FakeHttp(body='{"data": {"id": 1}}')
    from langchain_core.messages import AIMessage

    class TwiceWriteLlm:
        def __init__(self): self.round = 0
        def select(self, r): return self
        def bind_tools(self, t): return self
        async def astream(self, m):
            self.round += 1
            if self.round <= 2:
                yield json_tool_msg("approve_training_create", {"datasetId": 3})
            else:
                yield AIMessage(content="ok")

    registry = ToolRegistry()
    registry.load([make_tool("training_create", method="POST", path="/api/training/jobs")])
    graph = build_graph(llm_runtime=TwiceWriteLlm(), http=http, registry=registry,
                        client=client)

    final, _ = await run_graph(graph, TaskContext(task_id="t", task_token="tok"), [], max_rounds=10)

    # approve_* 不走只读重复检测（也会被写工具安全边界拦下重复，但走另一路径）
    # 此处主要验证不报 400 重复提示
    tool_msgs = [m for m in final if m.type == "tool"]
    assert not any("已检测到对" in m.content for m in tool_msgs)