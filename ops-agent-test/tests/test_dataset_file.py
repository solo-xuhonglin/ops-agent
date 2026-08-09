"""Tier 2 - Weather collection + file link verification.

The old upload endpoint (POST /api/datasets/{id}/file) was removed as dead
code; datasets get their data from explicit weather collection instead:
  POST /api/datasets/{id}/collect

Verifies the collect pipeline *really* worked against MinIO:
  1. collect returns 200 and the dataset lands READY with rowCount > 0
  2. objectKey points to {id}/weather.csv (written by the collector)
  3. the presigned URL endpoint successfully signs a URL (backend <-> MinIO OK)

We deliberately do NOT download/byte-compare the presigned URL: the backend signs
URLs with MINIO_ENDPOINT (docker-internal, e.g. http://minio:9000), so a direct
download from the test machine usually fails with a DNS/connection error. The
objectKey + successfully-issued signed URL are sufficient proof that the collect
pipeline executed end-to-end against MinIO.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.tier2


def test_collect_weather_and_verify(client, make_dataset):
    """Explicit re-collection really fetches data and updates the dataset."""
    ds = make_dataset()  # create also auto-collects; we re-collect explicitly
    res = client.collect_dataset(ds["id"])
    # collect contract: dataset updated with weather rows + READY status
    assert res["id"] == ds["id"]
    assert res["objectKey"] == f"{ds['id']}/weather.csv", \
        f"collect must write {ds['id']}/weather.csv, got {res.get('objectKey')}"
    assert res["status"] == "READY", f"collect should leave dataset READY, got {res.get('status')}"
    assert isinstance(res["rowCount"], int) and res["rowCount"] > 0, \
        f"collect must produce weather rows, got rowCount={res.get('rowCount')}"

    # detail now reflects the collected objectKey
    got = client.get_dataset(ds["id"])
    assert got["objectKey"] == f"{ds['id']}/weather.csv"

    # presigned URL is actually issued (backend reached MinIO successfully)
    info = client.file_url(ds["id"])
    assert info.get("url"), "presigned URL must be returned"
    assert info["objectKey"] == f"{ds['id']}/weather.csv"


def test_delete_after_collect_really_gone(client, make_dataset):
    """Deleting a dataset that has collected weather data.

    Backend delete purges the associated MinIO object (DatasetService.delete),
    so the file is gone too - not just the DB row. We verify the *record* is really
    gone: detail 404, file association 404, and absent from the list.
    """
    ds = make_dataset()
    client.collect_dataset(ds["id"])

    ds_id = ds["id"]
    client.delete_dataset(ds_id)

    # record gone
    with pytest.raises(Exception) as exc:
        client.get_dataset(ds_id)
    assert "404" in str(exc.value)

    # file association gone too (detail missing -> file/url 404)
    with pytest.raises(Exception) as exc2:
        client.file_url(ds_id)
    assert "404" in str(exc2.value)

    # list no longer contains it
    page = client.list_datasets()
    ids = [d["id"] for d in page["content"]]
    assert ds_id not in ids
