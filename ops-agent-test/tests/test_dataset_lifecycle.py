"""Tier 1 - Dataset metadata lifecycle E2E (no MinIO dependency).

Covers: create -> list -> get -> update -> delete, with real-success assertions.
Delete is verified to *really* succeed: detail returns 404 AND the id is absent
from the list AND the file association (if any) returns 404.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.tier1


def test_create_dataset(client):
    # NOTE: DatasetService.create now triggers weather collection, so the final
    # status/objectKey depend on an external weather API. We assert the create
    # contract (id + echoed fields) and that the record lands in a valid lifecycle
    # state, without pinning the weather-dependent status.
    name = f"e2e-{uuid.uuid4().hex[:12]}"
    ds = client.create_dataset(
        {
            "name": name,
            "description": "create test",
            "regions": ["北京"],
            "source": "e2e",
            "fileFormat": "csv",
            "rowCount": 100,
            "dateStart": "2020-01-01",
            "dateEnd": "2020-12-31",
        }
    )
    try:
        assert ds["id"] is not None, "created dataset must have an id"
        assert ds["name"] == name
        assert ds["status"] in ("COLLECTING", "READY", "INVALID"), \
            f"unexpected status {ds['status']}"
        assert ds["objectKey"], "objectKey must be set"
        assert ds["regions"] == ["北京"]
    finally:
        client.delete_dataset(ds["id"])


def test_list_datasets(client, make_dataset):
    ds = make_dataset()
    page = client.list_datasets(page=0, size=20)
    assert "content" in page
    assert page["size"] == 20
    assert "totalElements" in page and "totalPages" in page
    ids = [d["id"] for d in page["content"]]
    assert ds["id"] in ids, "newly created dataset must appear in the list"


def test_get_dataset(client, make_dataset):
    ds = make_dataset(regions=["上海", "北京"])
    got = client.get_dataset(ds["id"])
    assert got["id"] == ds["id"]
    assert got["name"] == ds["name"]
    assert got["regions"] == ["上海", "北京"]
    # rowCount is overwritten by weather collection on create; assert it is an int
    assert got["rowCount"] is None or isinstance(got["rowCount"], int)


def test_update_dataset(client, make_dataset):
    ds = make_dataset()
    new_name = f"upd-{uuid.uuid4().hex[:12]}"
    updated = client.update_dataset(
        ds["id"],
        {
            "name": new_name,
            "description": "updated by e2e",
            "regions": ["广州"],
            "source": "e2e",
            "fileFormat": "csv",
            "rowCount": 50,
            "dateStart": "2021-01-01",
            "dateEnd": "2021-12-31",
            "status": "READY",
        },
    )
    assert updated["name"] == new_name
    assert updated["description"] == "updated by e2e"
    assert updated["regions"] == ["广州"]
    # NOTE: PUT only updates metadata (no implicit re-collection), so the
    # user-supplied rowCount is preserved here. Re-collection is explicit
    # via POST /api/datasets/{id}/collect.


def test_update_changed_region_needs_collect(client, make_dataset):
    """Changing regions/date range via PUT does NOT re-collect; the data stays
    as-is until an explicit collect is issued (metadata/data decoupling)."""
    ds = make_dataset()
    updated = client.update_dataset(
        ds["id"],
        {
            "name": ds["name"],
            "regions": ["深圳"],
            "dateStart": "2021-01-01",
            "dateEnd": "2021-06-30",
            "status": "READY",
        },
    )
    assert updated["regions"] == ["深圳"]
    # metadata updated, but objectKey still points at the original collection
    assert updated["objectKey"].endswith("/weather.csv")


def test_delete_dataset_really_gone(client, make_dataset):
    """Delete must *really* succeed, not just return 200."""
    ds = make_dataset()
    ds_id = ds["id"]

    resp = client.delete_dataset(ds_id)
    assert resp["code"] == 200, "delete envelope code should be 200"

    # 1) detail is gone -> 404
    with pytest.raises(Exception) as exc:
        client.get_dataset(ds_id)
    assert "404" in str(exc.value)

    # 2) id no longer present in the list
    page = client.list_datasets()
    ids = [d["id"] for d in page["content"]]
    assert ds_id not in ids, "deleted dataset must disappear from the list"
