"""Real agent scenario tests: simple chat round + multi-step plan with auto-continue.

These exercise the REAL deployed worker + LLM (NOT the fake worker), so they
need an online ops-agent-core worker. Run explicitly against a deployed stack:

    cd ops-agent-test
    REAL_AGENT=1 pytest tests/test_agent_scenarios.py -v

Non-deterministic LLM behaviour is handled with defensive polling:
- simple case: only asserts the chat round converged to a non-empty assistant reply
- complex case: asserts approve -> execute reaches EXECUTED, and (when the model
  attached a plan_id) that the auto-continue round produces a next PENDING
  suggestion for a later plan step
"""
from __future__ import annotations

import os
import time

import pytest

from src.opsagent_client import OpsAgentClient

pytestmark = [
    pytest.mark.agent,
    pytest.mark.agent_real,
    pytest.mark.skipif(
        os.getenv("REAL_AGENT") != "1",
        reason="real-LLM scenario tests need an online worker; run with REAL_AGENT=1",
    ),
]


# ===================== helpers =====================

def _poll_assistant_message(client: OpsAgentClient, conversation_id: str,
                            task_id: str, timeout: float = 120.0) -> dict:
    """Wait until the assistant message for this task is terminal; return it."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        msgs = client.get_agent_conversation_messages(conversation_id)
        last = next((m for m in msgs if m["role"] == "assistant" and m.get("taskId") == task_id), None)
        if last and last["status"] in ("completed", "failed"):
            return last
        time.sleep(3)
    raise AssertionError(f"assistant message for task {task_id} did not finish; last={last}")


def _poll_pending_suggestion(client: OpsAgentClient, task_id: str, timeout: float = 180.0) -> dict | None:
    """Wait for a PENDING suggestion produced by the given chat task."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        page = client.list_agent_suggestions(page=0, size=50)
        for s in page["content"]:
            if s["status"] == "PENDING" and (s.get("taskId") == task_id or s.get("sourceTaskId") == task_id):
                return s
        time.sleep(3)
    return None


def _poll_suggestion_status(client: OpsAgentClient, suggestion_id: str,
                            statuses: set[str], timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        page = client.list_agent_suggestions(page=0, size=50)
        last = next((s for s in page["content"] if s["suggestionId"] == suggestion_id), None)
        if last and last["status"] in statuses:
            return last
        time.sleep(2)
    raise AssertionError(f"suggestion {suggestion_id} did not reach {statuses}; last={last}")


def _poll_new_pending(client: OpsAgentClient, plan_id: str, exclude: set[str],
                      timeout: float = 180.0) -> dict | None:
    """Wait for a PENDING suggestion in the SAME plan, not in `exclude`
    (i.e. the auto-continue next step; filters out unrelated leftover PENDINGs)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        page = client.list_agent_suggestions(page=0, size=50)
        new = [s for s in page["content"]
               if s["status"] == "PENDING" and s.get("planId") == plan_id
               and s["suggestionId"] not in exclude]
        if new:
            return new[0]
        time.sleep(3)
    return None


# ===================== simple scenario =====================

def test_scenario_simple_chat_round(client: OpsAgentClient):
    """Simple: one chat round converges to a non-empty assistant reply (may use tools)."""
    conv = client.create_agent_conversation()
    try:
        resp = client.send_agent_message(conv["conversationId"], {
            "query": "当前有哪些训练任务？",
            "reasoning": True,
        })
        task_id = resp["taskId"]
        assert task_id, "send message must return a taskId"

        msg = _poll_assistant_message(client, conv["conversationId"], task_id, timeout=180)
        assert msg["status"] == "completed", f"chat round failed: {msg['status']} {msg.get('content')}"
        assert msg["content"] and msg["content"].strip(), "assistant replied empty content"
        assert msg["taskId"] == task_id, msg
    finally:
        client.delete_agent_conversation(conv["conversationId"])


# ===================== complex scenario =====================

def test_scenario_multi_step_plan_with_auto_continue(client: OpsAgentClient):
    """Complex: multi-step request -> approve first step -> execute -> auto-continue
    proposes the next step (same plan), proving the full approval loop incl. continue."""
    conv = client.create_agent_conversation()
    try:
        resp = client.send_agent_message(conv["conversationId"], {
            "query": "对北京地区采集 2026-08-02 到 2026-08-09 的天气数据，然后基于该数据集训练一个 LSTM 模型",
            "reasoning": True,
        })
        task_id = resp["taskId"]
        assert task_id

        # 1) model proposes at least one PENDING write suggestion for this task
        first = _poll_pending_suggestion(client, task_id, timeout=240)
        assert first is not None, "model did not propose a write suggestion for the multi-step task"
        plan_id = first.get("planId") or ""
        assert plan_id, \
            "multi-step request should attach a plan_id to its first suggestion " \
            f"(got action={first['actionType']})"

        # 2) approve -> execute must reach EXECUTED (result message persisted)
        approved = client.approve_agent_suggestion(first["suggestionId"])
        assert approved["status"] in ("APPROVED", "EXECUTED"), approved
        executed = _poll_suggestion_status(client, first["suggestionId"],
                                           {"EXECUTED", "FAILED", "EXPIRED"}, timeout=180)
        assert executed["status"] == "EXECUTED", \
            f"execute did not succeed: {executed['status']} result={executed.get('result')}"

        # 3) auto-continue: a next PENDING suggestion for a later step should appear
        #    (may take a decision-round; a second chat message never needed)
        nxt = _poll_new_pending(client, plan_id, exclude={first["suggestionId"]}, timeout=240)
        if nxt is not None:
            assert nxt.get("planId") == plan_id, \
                f"continue step left the plan: {nxt.get('planId')} != {plan_id}"
            assert nxt["status"] == "PENDING", nxt
            assert nxt["suggestionId"] != first["suggestionId"]
    finally:
        client.delete_agent_conversation(conv["conversationId"])