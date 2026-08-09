"""Negative / auth / permission-path cases for the model & training APIs."""
from __future__ import annotations

import pytest

from src.opsagent_client import OpsAgentError

pytestmark = pytest.mark.negative


def test_models_list_requires_auth(base_url):
    """Without a bearer token, model listing must reject with 401."""
    anon = __import__("src.opsagent_client", fromlist=["OpsAgentClient"]).OpsAgentClient(
        base_url=base_url, username="x", password="y"
    )
    with pytest.raises(Exception) as exc:
        anon.list_models()
    assert "401" in str(exc.value)


def test_model_write_forbidden_for_reader(reader_client):
    """READONLY role lacks model:write -> deleting a model must be 403."""
    with pytest.raises(OpsAgentError) as exc:
        reader_client.delete_model(9_999_999)
    assert exc.value.status_code == 403


def test_training_trigger_forbidden_for_reader(reader_client):
    """READONLY role lacks training:write -> triggering a job must be 403."""
    with pytest.raises(OpsAgentError) as exc:
        reader_client.create_training_job({
            "datasetId": 1,
            "name": "x",
            "version": "v1",
        })
    assert exc.value.status_code == 403


def test_training_job_list_allowed_for_reader(reader_client):
    """READONLY role holds training:read -> listing jobs is allowed (200)."""
    page = reader_client.list_training_jobs()
    assert "content" in page, "reader (training:read) must be able to list training jobs"
