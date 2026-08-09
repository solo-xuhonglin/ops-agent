"""Agent module contract tests (no worker required).

Covers the management-plane HTTP contract only:
- AgentToolController  (/api/agent/tools): list + enabled toggle + validation
- AgentTaskController   (/api/agent/tasks): list pagination + 404 for missing id
- AgentSuggestionController (/api/agent/suggestions): list + 404 for missing id

The full task/suggestion *cycle* (dispatch -> events -> SUCCEEDED -> suggestion
persisted -> approve/reject) lives in test_agent_worker.py because it needs a
controlled gRPC worker (the real ops-agent-core worker may behave differently,
e.g. it actually calls an LLM). Those tests run only under AGENT_E2E=1.
"""
from __future__ import annotations

import uuid

import pytest

from src.opsagent_client import OpsAgentError, OpsAgentClient

pytestmark = pytest.mark.agent


# ===================== tools =====================

def test_agent_tools_list(client: OpsAgentClient):
    """Seeded tool registry is returned with all contract fields."""
    tools = client.list_agent_tools()
    assert isinstance(tools, list) and len(tools) >= 15, f"expected >=15 seeded tools, got {len(tools)}"
    names = {t["name"] for t in tools}
    for expected in (
        "dataset_list", "dataset_get", "model_list", "model_get",
        "training_list", "training_get", "training_get_logs_url",
        "serving_list", "serving_get", "serving_predict",
        "training_create", "training_delete", "serving_deploy", "serving_undeploy",
    ):
        assert expected in names, f"seeded tool missing: {expected}"
    # write tools must be flagged
    by_name = {t["name"]: t for t in tools}
    assert by_name["training_create"]["isWrite"] is True
    assert by_name["training_create"]["httpMethod"] == "POST"
    assert by_name["serving_predict"]["isWrite"] is False
    # every tool carries the schema / permission / path fields
    for t in tools:
        assert t["name"] and t["description"], t
        assert t["httpMethod"] in ("GET", "POST", "PUT", "DELETE"), t["name"]
        assert t["pathTemplate"].startswith("/api/"), t["name"]
        assert t["paramsSchema"], t["name"]
        assert isinstance(t["enabled"], bool), t["name"]


def test_agent_tool_toggle_enabled(client: OpsAgentClient):
    """Disabling a tool takes effect immediately and is reversible."""
    tools = client.list_agent_tools()
    tool = next(t for t in tools if t["name"] == "serving_predict")
    tool_id = tool["id"]
    original = tool["enabled"]
    try:
        # disable
        updated = client.set_agent_tool_enabled(tool_id, False)
        assert updated["enabled"] is False
        got = next(t for t in client.list_agent_tools() if t["id"] == tool_id)
        assert got["enabled"] is False, "disable must persist in the registry"
        # re-enable
        updated = client.set_agent_tool_enabled(tool_id, True)
        assert updated["enabled"] is True
        got = next(t for t in client.list_agent_tools() if t["id"] == tool_id)
        assert got["enabled"] is True, "re-enable must persist in the registry"
    finally:
        if original is not True:
            client.set_agent_tool_enabled(tool_id, original)


def test_agent_tool_toggle_invalid_enabled(client: OpsAgentClient):
    """Non-boolean enabled -> 422; unknown tool id -> 404."""
    tools = client.list_agent_tools()
    tool_id = tools[0]["id"]
    with pytest.raises(OpsAgentError) as exc:
        client.set_agent_tool_enabled(tool_id, "yes")  # type: ignore[arg-type]
    assert exc.value.status_code == 422, f"expected 422, got {exc.value.status_code}"
    with pytest.raises(OpsAgentError) as exc:
        client.set_agent_tool_enabled(99999999, True)
    assert exc.value.status_code == 404, f"expected 404, got {exc.value.status_code}"


# ===================== tasks =====================

def test_agent_tasks_list_pagination(client: OpsAgentClient):
    page = client.list_agent_tasks(page=0, size=20)
    assert "content" in page and "totalElements" in page and "totalPages" in page
    assert page["size"] == 20
    assert isinstance(page["content"], list)
    # any rows present must carry the status machine fields
    for t in page["content"]:
        assert t["taskId"] and t["status"] in (
            "DISPATCHED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"), t
        assert t["taskType"], t["taskId"]


def test_agent_task_get_nonexistent_404(client: OpsAgentClient):
    with pytest.raises(OpsAgentError) as exc:
        client.get_agent_task(f"no-such-{uuid.uuid4().hex}")
    assert exc.value.status_code == 404, f"expected 404, got {exc.value.status_code}"


# ===================== suggestions =====================

def test_agent_suggestions_list_pagination(client: OpsAgentClient):
    page = client.list_agent_suggestions(page=0, size=20)
    assert "content" in page and "totalElements" in page and "totalPages" in page
    assert isinstance(page["content"], list)
    for s in page["content"]:
        assert s["status"] in (
            "PENDING", "APPROVED", "REJECTED", "EXECUTING", "EXECUTED", "FAILED", "EXPIRED"), s
        assert s["actionType"] and s["targetType"], s


def test_agent_suggestion_approve_nonexistent_404(client: OpsAgentClient):
    with pytest.raises(OpsAgentError) as exc:
        client.approve_agent_suggestion(99999999)
    assert exc.value.status_code == 404, f"expected 404, got {exc.value.status_code}"


def test_agent_suggestion_reject_nonexistent_404(client: OpsAgentClient):
    with pytest.raises(OpsAgentError) as exc:
        client.reject_agent_suggestion(99999999)
    assert exc.value.status_code == 404, f"expected 404, got {exc.value.status_code}"
