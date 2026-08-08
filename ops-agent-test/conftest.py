"""Shared pytest fixtures for the ops-agent E2E suite.

Key design points:
- One authenticated client per session (login once, reuse token).
- `make_dataset` creates a dataset with a unique name and ALWAYS cleans it up
  in teardown, so remote/production data is never polluted.
- `base_url` is exposed for negative tests that need an unauthenticated client.
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest
from dotenv import load_dotenv

from src.opsagent_client import DEFAULT_BASE_URL, OpsAgentClient

load_dotenv()

BASE_URL = os.getenv("BASE_URL", DEFAULT_BASE_URL)


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(autouse=True, scope="session")
def _preflight(base_url):
    """Skip the whole session cleanly when the target backend is unreachable
    (e.g. remote host blocked from CI, or local stack not started)."""
    try:
        httpx.get(base_url.rstrip("/") + "/auth/login", timeout=5)
    except Exception as e:  # noqa: BLE001 - any connection failure => env not ready
        pytest.skip(
            f"backend unreachable at {base_url} ({e}); set BASE_URL or start a local "
            f"stack (docker compose up -d) before running."
        )


@pytest.fixture(scope="session")
def client() -> OpsAgentClient:
    c = OpsAgentClient()
    c.login()
    yield c
    c.close()


def _sample_payload(**overrides) -> dict:
    payload = {
        "name": f"e2e-{uuid.uuid4().hex[:12]}",
        "description": "e2e automated test dataset",
        "regions": ["北京"],
        "source": "e2e",
        "fileFormat": "csv",
        "rowCount": 100,
        "dateStart": "2020-01-01",
        "dateEnd": "2020-12-31",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def make_dataset(client: OpsAgentClient):
    """Factory that creates a dataset and guarantees teardown deletion."""
    created_ids: list[int] = []

    def _make(**overrides) -> dict:
        ds = client.create_dataset(_sample_payload(**overrides))
        created_ids.append(ds["id"])
        return ds

    yield _make

    # cleanup: best-effort delete so remote data stays clean
    for ds_id in created_ids:
        try:
            client.delete_dataset(ds_id)
        except Exception:
            # already deleted by the test, or transient error -> ignore in teardown
            pass
