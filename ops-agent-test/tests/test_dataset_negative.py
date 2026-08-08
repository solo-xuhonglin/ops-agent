"""Negative / auth-path cases for the dataset API."""
from __future__ import annotations

import os
import tempfile

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


def test_upload_to_missing_id_returns_404(client):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "wb") as f:
        f.write(b"a,b\n1,2\n")
    try:
        with pytest.raises(Exception) as exc:
            client.upload_file(9_999_999, path)
        assert "404" in str(exc.value)
    finally:
        os.unlink(path)
