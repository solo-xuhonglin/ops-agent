"""Agent module negative tests: auth & permission boundaries.

The seeded readOnly role (user/user123) holds agent:read but NOT agent:write
(verified in DataInitializer BUSINESS_READ_CODES), so:
- unauthenticated -> 401 on every agent endpoint
- user can READ (tools/tasks/suggestions list -> 200)
- user is FORBIDDEN (403) on agent:write endpoints (dispatch / approve / reject / toggle)
"""
from __future__ import annotations

import httpx
import pytest

from src.opsagent_client import OpsAgentClient

pytestmark = pytest.mark.negative


def test_agent_list_requires_auth(base_url):
    with pytest.raises(httpx.HTTPStatusError) as exc:
        httpx.get(base_url.rstrip("/") + "/api/agent/tools", timeout=10).raise_for_status()
    assert exc.value.response.status_code == 401, f"expected 401, got {exc.value.response.status_code}"


def test_agent_read_allowed_for_reader(reader_client: OpsAgentClient):
    """user holds agent:read -> the read endpoints must NOT be blocked."""
    tools = reader_client.list_agent_tools()
    assert isinstance(tools, list) and len(tools) >= 1
    page = reader_client.list_agent_tasks(page=0, size=10)
    assert "content" in page
    page = reader_client.list_agent_suggestions(page=0, size=10)
    assert "content" in page


def test_agent_dispatch_forbidden_for_reader(reader_client: OpsAgentClient):
    with pytest.raises(Exception) as exc:
        reader_client.dispatch_agent_task({"taskType": "question", "query": "x"})
    assert getattr(exc.value, "status_code", None) == 403, f"expected 403, got {exc.value}"


def test_agent_approve_forbidden_for_reader(reader_client: OpsAgentClient):
    with pytest.raises(Exception) as exc:
        reader_client.approve_agent_suggestion(1)
    assert getattr(exc.value, "status_code", None) == 403, f"expected 403, got {exc.value}"


def test_agent_reject_forbidden_for_reader(reader_client: OpsAgentClient):
    with pytest.raises(Exception) as exc:
        reader_client.reject_agent_suggestion(1)
    assert getattr(exc.value, "status_code", None) == 403, f"expected 403, got {exc.value}"


def test_agent_tool_toggle_forbidden_for_reader(reader_client: OpsAgentClient):
    # reader holds agent:read -> can read tools, but the write (enabled toggle) is 403
    tools = reader_client.list_agent_tools()
    tool_id = tools[0]["id"]
    with pytest.raises(Exception) as exc:
        reader_client.set_agent_tool_enabled(tool_id, False)
    assert getattr(exc.value, "status_code", None) == 403, f"expected 403, got {exc.value}"
