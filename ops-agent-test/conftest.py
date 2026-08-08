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


@pytest.fixture(scope="session")
def reader_client() -> OpsAgentClient:
    """Low-privilege client (user/user123) holding only dataset:read + model:read.
    Used to verify that model:/training: write endpoints return 403."""
    c = OpsAgentClient(username="user", password="user123")
    c.login()
    yield c
    c.close()


def _make_training_csv(path: str, n_per_region: int = 30) -> None:
    """Write a CSV in the schema train.py expects:
    region,time,temperature,precipitation — with enough rows per region
    (need > seq_len windows)."""
    import csv

    regions = ["北京", "上海"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["region", "time", "temperature", "precipitation"])
        for region in regions:
            for i in range(n_per_region):
                temp = 20.0 + (i % 15)
                precip = float(i % 5)
                w.writerow([region, f"2020-01-{i + 1:02d}T00:00:00", temp, precip])


@pytest.fixture
def training_csv(tmp_path) -> str:
    """A real weather CSV (region/time/temperature/precipitation) usable as a
    training dataset file. Lives in a temp dir, auto-cleaned by pytest."""
    p = tmp_path / "weather_train.csv"
    _make_training_csv(str(p), n_per_region=30)
    return str(p)
