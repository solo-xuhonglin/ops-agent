"""Controlled fake agent worker for the E2E suite.

Runs INSIDE the ops-agent-core image (has grpc + the generated stubs) on the
remote host, attached to the docker network where the admin gRPC server
(admin:9090) is reachable:

    docker run --rm --name e2e-fake-worker \
      --network <opsnet> \
      -v /opt/ops-agent/ops-agent-core:/app \
      -v /tmp/e2e:/e2e \
      ops-agent-core:latest python3 /e2e/fake_worker.py

Behaviour (deterministic, no LLM):
- Registers as worker `e2e-fake-<suffix>` with one agent; writes /e2e/ready
  once RegisterAck arrives (runner polls this file to know registration done).
- On TaskDispatch:
    * always sends one progress TaskEvent first
    * if the query contains "e2e-suggest" -> TaskResult ok=true with one
      Suggestion(action_type=serving_undeploy, target=serving_endpoint/999)
      -> this is what lets tests exercise the approve/reject suggestion flow
    * otherwise -> TaskResult ok=true, conclusion "e2e ok"
- On AuthorizationGrant -> appends the grant (JSON) to /e2e/grants.log
  (approve() pushes it down the stream; the runner reads the file to prove the
  grant really reached the worker).
- Answers Ping with Pong so the registry keeps us alive.
- Appends every dispatched task id to /e2e/results.log for the runner report.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

sys.path.insert(0, "/app")

import grpc  # noqa: E402

from app.transport import agent_pb2, agent_pb2_grpc  # noqa: E402

E2E_DIR = "/e2e"
READY_FILE = os.path.join(E2E_DIR, "ready")
GRANT_FILE = os.path.join(E2E_DIR, "grants.log")
RESULT_FILE = os.path.join(E2E_DIR, "results.log")
LOG_FILE = os.path.join(E2E_DIR, "worker.log")
ADMIN_ADDR = os.getenv("ADMIN_GRPC_ADDR", "admin:9090")
WORKER_ID = "e2e-fake-" + os.getenv("E2E_WORKER_SUFFIX", uuid.uuid4().hex[:8])
SUGGEST_MARKER = "e2e-suggest"


def _log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{time.time():.3f} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def _suggestion() -> agent_pb2.Suggestion:
    return agent_pb2.Suggestion(
        action_type="serving_undeploy",
        target_type="serving_endpoint",
        target_id=999,
        params="{}",
        reason="e2e fake suggestion",
        priority="HIGH",
    )


async def main() -> None:
    channel = grpc.aio.insecure_channel(ADMIN_ADDR)
    stub = agent_pb2_grpc.AgentServiceStub(channel)
    stream = stub.Connect()
    await stream.write(agent_pb2.ClientMessage(
        register=agent_pb2.Register(
            worker_id=WORKER_ID,
            agents=[agent_pb2.AgentInfo(agent_id="e2e-fake", capabilities=["diagnose"])],
        )))
    _log(f"registering worker={WORKER_ID} addr={ADMIN_ADDR}")

    seq = 0
    async for msg in stream:
        kind = msg.WhichOneof("msg")
        if kind == "register_ack":
            ack = msg.register_ack
            with open(READY_FILE, "w") as f:
                f.write(f"worker={WORKER_ID} ok={ack.ok} tools={len(ack.tools)}\n")
            _log(f"register ack ok={ack.ok} tools={len(ack.tools)}")
        elif kind == "ping":
            await stream.write(agent_pb2.ClientMessage(
                pong=agent_pb2.Pong(ts=msg.ping.ts)))
        elif kind == "task_dispatch":
            td = msg.task_dispatch
            seq += 1
            _log(f"task_dispatch task={td.task_id} type={td.task_type}")
            with open(RESULT_FILE, "a") as f:
                f.write(f"{td.task_id}\t{td.task_type}\t{td.target_type}\t{td.target_id}\n")
            # progress event
            await stream.write(agent_pb2.ClientMessage(
                task_event=agent_pb2.TaskEvent(
                    task_id=td.task_id, seq=seq, event_type="progress",
                    content="e2e worker analyzing")))
            # result (with a suggestion when the query asks for one)
            if SUGGEST_MARKER in (td.query or ""):
                await stream.write(agent_pb2.ClientMessage(
                    task_result=agent_pb2.TaskResult(
                        task_id=td.task_id, ok=True, conclusion="e2e ok with suggestion",
                        suggestions=[_suggestion()])))
            else:
                await stream.write(agent_pb2.ClientMessage(
                    task_result=agent_pb2.TaskResult(
                        task_id=td.task_id, ok=True, conclusion="e2e ok")))
            _log(f"task done task={td.task_id}")
        elif kind == "authorization_grant":
            g = msg.authorization_grant
            with open(GRANT_FILE, "a") as f:
                f.write(json.dumps({
                    "action": g.action_type, "target": g.target_type,
                    "targetId": g.target_id, "grantKey": g.grant_key,
                    "ttlSeconds": g.ttl_seconds,
                }) + "\n")
            _log(f"grant received action={g.action_type} key={g.grant_key}")
        else:
            _log(f"unhandled server message kind={kind}")


if __name__ == "__main__":
    asyncio.run(main())
