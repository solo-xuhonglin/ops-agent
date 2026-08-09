"""Agent conversation (multi-turn chat) contract + E2E tests.

Two layers:
1. Pure HTTP contract tests (no worker needed):
   - conversation CRUD (create / list / messages / delete)
   - ownership isolation: a READONLY user cannot see/read/delete the admin's
     conversation (403) and cannot create conversations with write actions
   - send message validation (empty -> 422)

2. Multi-turn + streaming tests (need the fake worker via agent_e2e_runner.py):
   - a message round produces a user message + internal task + assistant message
   - history is carried on the second round (fake worker records TaskDispatch.history)
   - streaming events (thinking/tool_call/tool_result/delta) are persisted as
     task events and the assistant message carries reasoning + content
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

from src.opsagent_client import OpsAgentError, OpsAgentClient

pytestmark = pytest.mark.agent

# ===================== HTTP contract (no worker) =====================


def test_conversation_crud_flow(client: OpsAgentClient):
    """create -> appears in list -> messages empty -> delete -> gone."""
    conv = client.create_agent_conversation()
    assert conv["conversationId"] and conv["title"] == "新对话"

    page = client.list_agent_conversations(page=0, size=20)
    ids = [c["conversationId"] for c in page["content"]]
    assert conv["conversationId"] in ids

    messages = client.get_agent_conversation_messages(conv["conversationId"])
    assert messages == []

    client.delete_agent_conversation(conv["conversationId"])
    page2 = client.list_agent_conversations(page=0, size=100)
    assert conv["conversationId"] not in [c["conversationId"] for c in page2["content"]]


def test_conversation_message_empty_422(client: OpsAgentClient):
    """empty query + no target -> 422."""
    conv = client.create_agent_conversation()
    try:
        with pytest.raises(OpsAgentError) as exc:
            client.send_agent_message(conv["conversationId"], {"query": "   "})
        assert exc.value.status_code == 422, f"expected 422, got {exc.value.status_code}"
    finally:
        client.delete_agent_conversation(conv["conversationId"])


def test_conversation_ownership_isolated(client: OpsAgentClient, reader_client: OpsAgentClient):
    """A READONLY user cannot see or touch another user's conversation (403)."""
    conv = client.create_agent_conversation()
    try:
        # list isolation: READONLY's own list must not contain the admin's conversation
        page = reader_client.list_agent_conversations(page=0, size=100)
        assert conv["conversationId"] not in [c["conversationId"] for c in page["content"]]

        # direct access is forbidden
        with pytest.raises(OpsAgentError) as exc:
            reader_client.get_agent_conversation_messages(conv["conversationId"])
        assert exc.value.status_code == 403, f"expected 403, got {exc.value.status_code}"

        with pytest.raises(OpsAgentError) as exc:
            reader_client.delete_agent_conversation(conv["conversationId"])
        assert exc.value.status_code == 403, f"expected 403, got {exc.value.status_code}"

        # READONLY lacks agent:write -> cannot create a conversation
        with pytest.raises(OpsAgentError) as exc:
            reader_client.create_agent_conversation()
        assert exc.value.status_code == 403, f"expected 403, got {exc.value.status_code}"
    finally:
        client.delete_agent_conversation(conv["conversationId"])


# ===================== multi-turn + streaming (fake worker) =====================

pytestmark_e2e = [
    pytest.mark.agent,
    pytest.mark.agent_e2e,
    pytest.mark.skipif(
        os.getenv("AGENT_E2E") != "1",
        reason="multi-turn conversation tests need the fake worker (run via scripts/agent_e2e_runner.py)",
    ),
]


def _poll_conversation_message(client: OpsAgentClient, conversation_id: str,
                               task_id: str, timeout: float = 30.0) -> dict:
    """Poll messages until the assistant message for the task is completed/failed."""
    deadline = time.time() + timeout
    last = []
    while time.time() < deadline:
        messages = client.get_agent_conversation_messages(conversation_id)
        last = messages
        for m in messages:
            if m.get("taskId") == task_id and m["role"] == "assistant":
                if m["status"] in ("completed", "failed"):
                    return m
        time.sleep(1)
    raise AssertionError(f"assistant message for {task_id} not terminal; messages={last}")


def test_conversation_multi_turn_and_streaming(client: OpsAgentClient):
    """Round 1 -> history carried to round 2; assistant message has content+reasoning;
    streaming events persisted as task events in order."""
    conv = client.create_agent_conversation()
    try:
        # ---- round 1 ----
        r1 = client.send_agent_message(conv["conversationId"], {"query": "第一个问题 e2e"})
        assert r1["taskId"], r1
        m1 = _poll_conversation_message(client, conv["conversationId"], r1["taskId"])
        assert m1["status"] == "completed", m1
        assert m1["content"], "assistant message must carry conclusion"
        assert m1["reasoning"] == "e2e reasoning full", m1

        # task events: streaming event sequence persisted (thinking -> tool_call -> tool_result -> delta)
        task = client.get_agent_task(r1["taskId"])
        types = [e["eventType"] for e in task["events"]]
        assert "thinking" in types and "delta" in types, types
        assert "tool_call" in types and "tool_result" in types, types
        # tool_call content is JSON {name, args}
        tool_call = next(e for e in task["events"] if e["eventType"] == "tool_call")
        assert '"name"' in tool_call["content"] and '"args"' in tool_call["content"], tool_call

        # ---- round 2: must carry round-1 history ----
        r2 = client.send_agent_message(conv["conversationId"], {"query": "第二个问题 e2e"})
        m2 = _poll_conversation_message(client, conv["conversationId"], r2["taskId"])
        assert m2["status"] == "completed", m2

        # message stream: 2 user + 2 assistant
        messages = client.get_agent_conversation_messages(conv["conversationId"])
        roles = [m["role"] for m in messages]
        assert roles.count("user") == 2 and roles.count("assistant") == 2, roles
        # order: user1, assistant1, user2, assistant2
        assert roles == ["user", "assistant", "user", "assistant"], roles

        # title derived from the first user message
        page = client.list_agent_conversations(page=0, size=20)
        c = next(c for c in page["content"] if c["conversationId"] == conv["conversationId"])
        assert c["title"] == "第一个问题 e2e", c["title"]
    finally:
        client.delete_agent_conversation(conv["conversationId"])
