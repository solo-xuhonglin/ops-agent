"""Negative / authorization cases for the model serving module.

- No token -> 401 on serving endpoints.
- The READONLY role (dynamically created in conftest) holds serving:read but
  NOT serving:write -> list is 200, deploy is 403.
"""
from __future__ import annotations

import httpx
import pytest

from src.opsagent_client import OpsAgentError

pytestmark = pytest.mark.negative


def test_serving_list_requires_auth(base_url):
    resp = httpx.get(base_url.rstrip("/") + "/api/serving/endpoints", timeout=10)
    assert resp.status_code == 401, f"expected 401 without token, got {resp.status_code}"


def test_serving_forbidden_for_reader(reader_client):
    # READONLY role holds serving:read -> listing is allowed (200)
    page = reader_client.list_serving_endpoints()
    assert "content" in page, "reader (serving:read) must be able to list endpoints"

    # but serving:write is missing -> deploy must be 403
    with pytest.raises(OpsAgentError) as exc:
        reader_client.deploy_serving(1)
    assert exc.value.status_code == 403, f"expected 403 on serving deploy, got {exc.value.status_code}"
