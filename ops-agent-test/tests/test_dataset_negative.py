"""Negative / auth-path cases for the dataset API."""
from __future__ import annotations

import pytest

from src.opsagent_client import OpsAgentClient

pytestmark = pytest.mark.negative


def test_list_requires_auth(base_url):
    """Without a bearer token, the list endpoint must reject with 401."""
    anon = OpsAgentClient(base_url=base_url, username="x", password="y")
    with pytest.raises(Exception) as exc:
        anon.list_datasets()
    assert "401" in str(exc.value)


def test_delete_nonexistent_returns_404(client):
    with pytest.raises(Exception) as exc:
        client.delete_dataset(9_999_999)
    assert "404" in str(exc.value)


def test_collect_missing_id_returns_404(client):
    with pytest.raises(Exception) as exc:
        client.collect_dataset(9_999_999)
    assert "404" in str(exc.value)


def test_create_missing_name_returns_400(client):
    """DTO validation: dataset name is required -> 400, not a DB-level 500."""
    with pytest.raises(Exception) as exc:
        client.create_dataset({"regions": ["北京"]})
    assert "400" in str(exc.value)


def test_create_bad_json_returns_400(client):
    """Unreadable request body -> 400 (HttpMessageNotReadableException handler)."""
    import httpx
    resp = client.http.post(
        "/api/datasets",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400, f"expected 400 for bad json, got {resp.status_code}"
