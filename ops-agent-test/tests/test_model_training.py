"""End-to-end tests for the model training module.

Covers:
- ModelVersion CRUD (create / get / list / delete / download-url).
- Training job lifecycle, including a REAL training run on the remote backend:
  create dataset + upload CSV -> trigger training (tiny hyperparams) -> poll until
  terminal -> assert ModelVersion becomes READY with artifact + metrics, and the
  logs URL is issued. This exercises the full docker-java pipeline and surfaces
  remote problems (e.g. missing ops-agent-train image, docker.sock access).
- Trigger validation: non-existent dataset -> 404; dataset without a real file is
  accepted but the run ends FAILED (no data object in MinIO -> container exits).
"""
from __future__ import annotations

import time
import uuid

import pytest

from src.opsagent_client import OpsAgentError

pytestmark = pytest.mark.model_training


# ===================== ModelVersion CRUD =====================
def test_model_version_crud(client):
    name = f"e2e-mv-{uuid.uuid4().hex[:10]}"
    mv = client.create_model({"name": name, "version": "v1", "algorithm": "LSTM"})
    assert mv["id"], "model version id should be returned"
    assert mv["name"] == name
    assert mv["status"] in (None, "DRAFT", "TRAINING", "READY", "FAILED")

    got = client.get_model(mv["id"])
    assert got["id"] == mv["id"]
    assert got["name"] == name

    listed = client.list_models()
    ids = [m["id"] for m in listed["content"]]
    assert mv["id"] in ids, "newly created model should appear in list"

    # an un-trained model has no artifact -> download url must error, not 200
    with pytest.raises(OpsAgentError) as exc:
        client.model_download_url(mv["id"])
    assert exc.value.status_code >= 400

    client.delete_model(mv["id"])
    with pytest.raises(OpsAgentError) as exc:
        client.get_model(mv["id"])
    assert "404" in str(exc.value)


# ===================== Real training run =====================
def test_trigger_training_real_run(client, make_dataset, training_csv):
    # 1) dataset must have a REAL uploaded file (weather:// placeholder is rejected)
    ds = make_dataset()
    upload = client.upload_file(ds["id"], training_csv)
    object_key = upload.get("objectKey", "")
    assert object_key and not object_key.startswith("weather://"), \
        "dataset must have a real data file before training"

    # 2) trigger a real training job with tiny hyperparams so it finishes fast
    req = {
        "datasetId": ds["id"],
        "name": f"e2e-model-{uuid.uuid4().hex[:8]}",
        "version": "v1",
        "algorithm": "LSTM",
        "hyperparameters": {
            "seqLen": 12,
            "hiddenSize": 16,
            "epochs": 2,
            "batchSize": 16,
            "lr": 0.01,
        },
    }
    try:
        job = client.create_training_job(req)
    except OpsAgentError as e:
        pytest.fail(
            f"trigger training failed on remote (possible missing train image / "
            f"docker.sock issue): {e}"
        )

    assert job["status"] == "RUNNING", f"job should be RUNNING after launch, got {job['status']}"
    job_id = job["id"]
    mv_id = job["modelVersionId"]

    # 3) poll until the TrainingJobPoller finalizes the job (every 5s)
    terminal = None
    for _ in range(48):  # up to ~4 min
        time.sleep(5)
        j = client.get_training_job(job_id)
        if j["status"] in ("SUCCEEDED", "FAILED"):
            terminal = j
            break
    assert terminal is not None, "training job did not reach a terminal state in time"
    assert terminal["status"] == "SUCCEEDED", \
        f"training ended {terminal['status']} (remote pipeline problem)"

    # 4) ModelVersion finalized: READY + artifact key + metrics written back
    mv = client.get_model(mv_id)
    assert mv["status"] == "READY", f"model version status={mv['status']}"
    assert mv["artifactKey"] and mv["artifactKey"].startswith("models/")
    assert mv["metrics"], "metrics.json should be written back to the model version"

    # 5) logs URL is issued once the job is done
    logs = client.training_logs_url(job_id)
    assert logs.get("url"), "training logs presigned url should be returned"

    # ---- cleanup (dataset handled by make_dataset teardown) ----
    client.delete_training_job(job_id)
    client.delete_model(mv_id)


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


def test_trigger_training_dataset_without_file_fails(client, make_dataset):
    # A dataset without an uploaded file gets a placeholder objectKey (e.g. "<id>/weather.csv")
    # and status INVALID. The trigger is accepted (200), but the training container cannot
    # find the data object in MinIO, so the job ends FAILED. We assert that end-to-end path.
    ds = make_dataset()
    job = client.create_training_job({
        "datasetId": ds["id"],
        "name": "no-file",
        "version": "v1",
    })
    job_id = job["id"]
    mv_id = job["modelVersionId"]
    assert job["status"] in ("PENDING", "RUNNING"), job["status"]

    terminal = None
    for _ in range(48):  # up to ~4 min
        time.sleep(5)
        j = client.get_training_job(job_id)
        if j["status"] in ("SUCCEEDED", "FAILED"):
            terminal = j
            break
    assert terminal is not None, "no-file training job did not reach a terminal state"
    assert terminal["status"] == "FAILED", \
        f"expected FAILED for dataset without a real data file, got {terminal['status']}"

    # cleanup: on failure the model version stays TRAINING and must be removed too
    try:
        client.delete_training_job(job_id)
    finally:
        client.delete_model(mv_id)
