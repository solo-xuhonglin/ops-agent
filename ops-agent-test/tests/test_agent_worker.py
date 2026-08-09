"""Agent full-cycle E2E tests (need a controlled fake worker).

These tests exercise the REAL end-to-end agent loop:
  dispatch -> gRPC TaskDispatch -> worker replies TaskEvent + TaskResult
           -> task SUCCEEDED -> suggestion persisted (PENDING)
           -> approve -> grantKey issued + pushed as AuthorizationGrant
           -> execute_suggestion task dispatched -> suggestion EXECUTED
           -> reject -> REJECTED (and double-reject is rejected again)

They only run when a fake worker is wired up by scripts/agent_e2e_runner.py
(env AGENT_E2E=1). The runner briefly stops the real ops-agent-core container
so the registry contains exactly one worker (ours) and dispatch is
deterministic; it restores the real agent afterwards.
"""
from __future__ import annotations

import os
import time

import pytest

from src.opsagent_client import OpsAgentClient

pytestmark = [
    pytest.mark.agent,
    pytest.mark.agent_e2e,
    pytest.mark.skipif(
        os.getenv("AGENT_E2E") != "1",
        reason="agent full-cycle tests need the fake worker (run via scripts/agent_e2e_runner.py)",
    ),
]


def _poll_task(client: OpsAgentClient, task_id: str, timeout: float = 30.0) -> dict:
    """Poll GET /api/agent/tasks/{id} until terminal; returns the {task, events} payload."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        payload = client.get_agent_task(task_id)
        last = payload
        if payload["task"]["status"] in ("SUCCEEDED", "FAILED"):
            return payload
        time.sleep(1)
    raise AssertionError(f"task {task_id} did not reach terminal state; last={last}")


def _find_suggestion(client: OpsAgentClient, task_id: str) -> dict:
    page = client.list_agent_suggestions(page=0, size=50)
    for s in page["content"]:
        if s.get("taskId") == task_id:
            return s
    raise AssertionError(f"no suggestion found for task {task_id} in {page['content']}")


def _poll_suggestion_status(client: OpsAgentClient, suggestion_id: str, statuses: set[str], timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        page = client.list_agent_suggestions(page=0, size=50)
        last = next((s for s in page["content"] if s["suggestionId"] == suggestion_id), None)
        if last and last["status"] in statuses:
            return last
        time.sleep(1)
    raise AssertionError(f"suggestion {suggestion_id} did not reach {statuses}; last={last}")


def test_agent_dispatch_full_cycle(client: OpsAgentClient):
    """dispatch -> worker event -> SUCCEEDED, with events + worker attribution."""
    resp = client.dispatch_agent_task({
        "taskType": "question",
        "targetType": "dataset",
        "targetId": 1,
        "query": "e2e hello",
    })
    task_id = resp["taskId"]
    assert task_id, "dispatch must return a taskId"
    assert resp["status"] in ("DISPATCHED", "RUNNING", "SUCCEEDED"), resp

    payload = _poll_task(client, task_id)
    task = payload["task"]
    assert task["status"] == "SUCCEEDED", f"expected SUCCEEDED, got {task['status']}"
    assert task["conclusion"] == "e2e ok", task["conclusion"]
    assert task["workerId"], "task must record which worker handled it"
    assert task["finishedAt"], "finishedAt must be set on success"
    assert task["taskType"] == "question", task["taskType"]

    events = payload["events"]
    assert events, "task must carry at least one event"
    assert events[0]["eventType"] == "progress", events
    assert events[0]["content"] == "e2e worker analyzing", events
    assert events[0]["taskId"] == task_id, events


def test_agent_suggestion_approve_and_execute_flow(client: OpsAgentClient):
    """suggestion PENDING -> approve -> APPROVED + grantKey -> execute task -> EXECUTED."""
    resp = client.dispatch_agent_task({
        "taskType": "diagnose_serving",
        "targetType": "serving_endpoint",
        "targetId": 999,
        "query": '{"e2e-suggest":1,"hint":"please suggest an undeploy"}',
    })
    task_id = resp["taskId"]
    _poll_task(client, task_id)

    sug = _find_suggestion(client, task_id)
    sug_id = sug["suggestionId"]  # approve/reject 端点的路径参数是 suggestionId（UUID）
    assert sug["status"] == "PENDING", f"new suggestion should be PENDING, got {sug['status']}"
    assert sug["actionType"] == "serving_undeploy", sug
    assert sug["targetType"] == "serving_endpoint", sug
    assert sug["targetId"] == 999, sug
    assert sug["priority"] == "HIGH", sug

    # approve -> grantKey issued (worker is online, so no AGENT_OFFLINE path)
    approved = client.approve_agent_suggestion(sug_id)
    assert approved["suggestionId"] == sug_id
    assert approved["status"] == "APPROVED", approved
    assert approved["grantKey"], "approve must return a grantKey"

    # the execute_suggestion task is dispatched by approve(); our worker answers
    # ok -> suggestion moves to EXECUTED with the task conclusion as result
    done = _poll_suggestion_status(client, sug_id, {"EXECUTED", "FAILED"})
    assert done["status"] == "EXECUTED", f"expected EXECUTED after execute task, got {done['status']}"
    assert done["result"] == "e2e ok", done.get("result")

    # double-approve: business error envelope (HTTP 200, body code 400, msg mentions PENDING)
    import httpx as _httpx  # noqa: PLC0415
    resp2 = client.http.post(f"/api/agent/suggestions/{sug_id}/approve")
    assert resp2.status_code == 200, f"business error should still be HTTP 200, got {resp2.status_code}"
    body2 = resp2.json()
    assert body2.get("code") == 400, body2
    assert "PENDING" in body2.get("message", ""), body2


def test_agent_suggestion_reject_flow(client: OpsAgentClient):
    """suggestion PENDING -> reject -> REJECTED; double-reject is a business error."""
    resp = client.dispatch_agent_task({
        "taskType": "question",
        "targetType": "serving_endpoint",
        "targetId": 999,
        "query": '{"e2e-suggest":1,"hint":"reject me"}',
    })
    task_id = resp["taskId"]
    _poll_task(client, task_id)

    sug = _find_suggestion(client, task_id)
    sug_id = sug["suggestionId"]
    assert sug["status"] == "PENDING", sug

    rejected = client.reject_agent_suggestion(sug_id)
    assert rejected["suggestionId"] == sug_id
    assert rejected["status"] == "REJECTED", rejected

    # double-reject -> business error envelope (HTTP 200, body code 400)
    resp2 = client.http.post(f"/api/agent/suggestions/{sug_id}/reject")
    assert resp2.status_code == 200, resp2.status_code
    body2 = resp2.json()
    assert body2.get("code") == 400, body2
    assert "PENDING" in body2.get("message", ""), body2
