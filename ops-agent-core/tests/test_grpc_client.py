import asyncio

import pytest

from app.config import Config
from app.transport import agent_pb2
from app.transport.grpc_client import GrpcClient


def make_client() -> GrpcClient:
    cfg = Config(worker_id="w1", admin_grpc_addr="localhost:9090")
    return GrpcClient(cfg, agents=[("ops-core", ["question"])])


def test_route_ping_replies_pong(monkeypatch):
    client = make_client()
    sent: list[agent_pb2.ClientMessage] = []

    async def fake_send(msg):
        sent.append(msg)

    async def gather():
        client._send = fake_send  # type: ignore[assignment]
        client._route(agent_pb2.ServerMessage(
            ping=agent_pb2.Ping(ts=12345)))
        await asyncio.sleep(0.05)

    asyncio.run(gather())
    assert len(sent) == 1
    assert sent[0].WhichOneof("msg") == "pong"
    assert sent[0].pong.ts == 12345


def test_route_dispatches_to_registered_handler(monkeypatch):
    client = make_client()
    received: list[agent_pb2.ServerMessage] = []

    async def handler(msg):
        received.append(msg)

    async def gather():
        client.on("task_dispatch", handler)
        client._route(agent_pb2.ServerMessage(
            task_dispatch=agent_pb2.TaskDispatch(task_id="t1", query="q")))
        await asyncio.sleep(0.05)

    asyncio.run(gather())
    assert len(received) == 1
    assert received[0].task_dispatch.task_id == "t1"


def test_route_ignores_unhandled_kind(monkeypatch):
    client = make_client()
    sent: list = []

    async def fake_send(msg):
        sent.append(msg)

    async def gather():
        client._send = fake_send  # type: ignore[assignment]
        client._route(agent_pb2.ServerMessage(
            cancel_task=agent_pb2.CancelTask(task_id="t1")))
        await asyncio.sleep(0.05)

    asyncio.run(gather())
    assert sent == []  # 无 handler 且非 ping → 忽略


@pytest.mark.asyncio
async def test_send_event_increments_seq():
    client = make_client()
    sent: list[agent_pb2.ClientMessage] = []

    async def fake_send(msg):
        sent.append(msg)

    client._send = fake_send  # type: ignore[assignment]
    await client.send_event("t1", "progress", "step1")
    await client.send_event("t1", "progress", "step2")
    assert [m.task_event.seq for m in sent] == [1, 2]
    assert sent[0].task_event.event_type == "progress"
