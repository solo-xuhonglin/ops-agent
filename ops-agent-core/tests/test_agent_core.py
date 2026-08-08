import asyncio

import pytest

from app.agent.core import handle_dispatch
from app.transport import agent_pb2


class FakeClient:
    def __init__(self):
        self.events = []
        self.results = []

    async def send_event(self, task_id, event_type, content):
        self.events.append((task_id, event_type, content))

    async def send_result(self, task_id, ok, conclusion, error=""):
        self.results.append((task_id, ok, conclusion, error))


@pytest.mark.asyncio
async def test_handle_dispatch_emits_events_and_result():
    client = FakeClient()
    msg = agent_pb2.ServerMessage(
        task_dispatch=agent_pb2.TaskDispatch(
            task_id="t-123", task_type="diagnose_serving",
            target_type="serving_endpoint", target_id=5))

    await handle_dispatch(client, msg)

    # 2 个进度事件 + 1 个结果
    assert len(client.events) == 2
    assert client.events[0][0] == "t-123"
    assert client.events[0][1] == "progress"
    assert "diagnose_serving" in client.events[0][2]

    assert len(client.results) == 1
    task_id, ok, conclusion, _ = client.results[0]
    assert task_id == "t-123"
    assert ok is True
    assert "t-123" in conclusion
