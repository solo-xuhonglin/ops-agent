"""End-to-end tests for the model serving module.

Covers:
- ServingEndpoint list / get / 404 for a missing endpoint.
- Deploy validation: non-READY model -> 422; missing model -> 404.
- The FULL serving pipeline (real remote): train a READY model -> deploy ->
  poll until DEPLOYED (ServingHealthPoller) -> /tools exposes it -> predict
  through the admin proxy -> undeploy -> STOPPED (idempotent) -> predict on a
  stopped endpoint -> 409 -> delete -> 404.
"""
from __future__ import annotations

import time
import uuid

import pytest

from src.opsagent_client import OpsAgentError

pytestmark = pytest.mark.serving


def _wait_status(client, ep_id: int, expected: set, timeout_s: int = 90, interval: int = 3) -> dict:
    last = None
    for _ in range(int(timeout_s / interval)):
        time.sleep(interval)
        last = client.get_serving_endpoint(ep_id)
        if last["status"] in expected:
            return last
    raise AssertionError(
        f"endpoint {ep_id} did not reach {expected} in {timeout_s}s, last={last and last['status']}"
    )


def test_serving_list_and_get(client):
    page = client.list_serving_endpoints(page=0, size=20)
    assert "content" in page and "totalElements" in page and "totalPages" in page

    # missing endpoint -> 404
    with pytest.raises(OpsAgentError) as exc:
        client.get_serving_endpoint(9_999_999)
    assert "404" in str(exc.value)


def test_deploy_non_ready_model_422(client):
    name = f"notready-{uuid.uuid4().hex[:8]}"
    mv = client.create_model({"name": name, "version": "v1", "algorithm": "LSTM", "status": "TRAINING"})
    try:
        with pytest.raises(OpsAgentError) as exc:
            client.deploy_serving(mv["id"])
        assert exc.value.status_code == 422, \
            f"expected 422 for non-READY model, got {exc.value.status_code}"
    finally:
        client.delete_model(mv["id"])


def test_deploy_missing_model_404(client):
    with pytest.raises(OpsAgentError) as exc:
        client.deploy_serving(9_999_999)
    assert "404" in str(exc.value)


def test_serving_full_lifecycle(client, ready_model):
    """Deploy a real READY model -> DEPLOYED -> /tools -> predict -> undeploy -> delete."""
    mv_id = ready_model["model_version_id"]

    # 1) deploy -> CREATING, referencing the model
    ep = client.deploy_serving(mv_id)
    ep_id = ep["id"]
    assert ep["status"] == "CREATING", ep["status"]
    assert ep["modelVersionId"] == mv_id

    try:
        # 2) poll until ServingHealthPoller marks it DEPLOYED (container /health OK)
        dep = _wait_status(client, ep_id, {"DEPLOYED", "FAILED"})
        assert dep["status"] == "DEPLOYED", \
            f"serving container not ready on remote: {dep['status']} (image missing? docker.sock?)"

        # 3) the deployed endpoint shows up in the list (status filter)
        listed = client.list_serving_endpoints(status="DEPLOYED")
        assert ep_id in [e["id"] for e in listed["content"]], "deployed endpoint must appear in list"

        # 4) predict through the admin proxy: values length must be >= seq_len (12)
        values = [20.0 + (i % 5) for i in range(20)]
        out = client.serving_predict(ep_id, values, horizon=3)
        assert str(out["modelVersionId"]) == str(mv_id)
        preds = out["predictions"]
        assert isinstance(preds, list) and len(preds) == 3, f"unexpected predictions {preds}"
        assert all(isinstance(p, (int, float)) for p in preds), "predictions must be numeric"

        # 5) undeploy -> STOPPED, and a second undeploy is idempotent
        stopped = client.undeploy_serving(ep_id)
        assert stopped["status"] == "STOPPED"
        again = client.undeploy_serving(ep_id)
        assert again["status"] == "STOPPED"

        # 6) predict on a stopped endpoint -> 409
        with pytest.raises(OpsAgentError) as exc:
            client.serving_predict(ep_id, values, horizon=1)
        assert exc.value.status_code == 409, \
            f"expected 409 for stopped endpoint, got {exc.value.status_code}"
    finally:
        # 7) delete the endpoint record (container already gone) -> 404 afterwards
        client.delete_serving_endpoint(ep_id)
        with pytest.raises(OpsAgentError) as exc:
            client.get_serving_endpoint(ep_id)
        assert "404" in str(exc.value)
