"""End-to-end tests for the model training module.

Covers:
- ModelVersion CRUD (create / get / list / delete / download-url).
- Training job lifecycle, including a REAL training run on the remote backend:
  create dataset + upload CSV -> trigger training (tiny hyperparams) -> poll until
  terminal -> assert ModelVersion becomes READY with artifact + metrics, and the
  logs URL is issued. This exercises the full docker-java pipeline and surfaces
  remote problems (e.g. missing ops-agent-train image, docker.sock access).
- Trigger validation: non-existent dataset -> 404; a data-less dataset (no
  regions/dates, objectKey stays weather://...) is rejected with 422.
"""
from __future__ import annotations

import uuid

import pytest

from src.opsagent_client import OpsAgentError

pytestmark = pytest.mark.model_training


# ===================== ModelVersion read flows =====================
def test_model_version_read(client, ready_model):
    """ModelVersion is a training product: POST /api/models was removed, so
    only read flows exist. `ready_model` produces a real READY model."""
    mv = ready_model["model"]
    assert mv["id"] and mv["status"] == "READY"

    got = client.get_model(mv["id"])
    assert got["id"] == mv["id"]
    assert got["name"] == mv["name"]

    listed = client.list_models()
    ids = [m["id"] for m in listed["content"]]
    assert mv["id"] in ids, "trained model should appear in list"

    # READY model has an artifact -> download url works
    url = client.model_download_url(mv["id"])
    assert url["url"].startswith("http"), f"expected presigned url, got {url}"

    # missing model -> 404
    with pytest.raises(OpsAgentError) as exc:
        client.get_model(9_999_999)
    assert "404" in str(exc.value)


# ===================== Real training run =====================
def test_trigger_training_real_run(client, ready_model):
    # `ready_model` already did: dataset (weather-collected) -> trigger (tiny
    # hyperparams) -> poll until SUCCEEDED -> model READY. Assert the finalized
    # model + log access, then let the fixture teardown purge job/model.
    mv = ready_model["model"]
    assert mv["artifactKey"] and mv["artifactKey"].endswith("/model.pt"), \
        f"artifactKey should point to model.pt, got {mv.get('artifactKey')}"
    assert mv["metrics"], "metrics.json should be written back to the model version"

    logs = client.training_logs_url(ready_model["job_id"])
    assert logs.get("url"), "training logs presigned url should be returned"


# ===================== Trigger validation =====================
def test_trigger_training_missing_dataset_404(client, make_dataset):
    # a (separate) dataset is created just to confirm the factory works; we then
    # target a non-existent dataset id.
    make_dataset()
    with pytest.raises(OpsAgentError) as exc:
        client.create_training_job({
            "datasetId": 9_999_999,
            "name": "ghost",
            "version": "v1",
        })
    assert "404" in str(exc.value)


def test_trigger_training_dataset_without_data_errors(client, make_dataset):
    # A dataset created WITHOUT regions/dates gets no weather data and no uploaded
    # file: objectKey stays "weather://<name>", which trigger() rejects with 422
    # (IllegalArgumentException -> UNPROCESSABLE_ENTITY).
    ds = make_dataset(regions=[])
    with pytest.raises(OpsAgentError) as exc:
        client.create_training_job({
            "datasetId": ds["id"],
            "name": "no-data",
            "version": "v1",
        })
    assert exc.value.status_code == 422, f"expected 422 for data-less dataset, got {exc.value.status_code}"
