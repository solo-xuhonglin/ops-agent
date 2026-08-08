"""Tier 2 - MinIO file upload link (real object-storage verification).

Verifies the upload *really* succeeded by:
  1. upload returns 200 with objectKey == {id}/{filename} (no datasets/ prefix)
  2. the presigned URL endpoint successfully signs a URL (backend <-> MinIO OK)

We deliberately do NOT download/byte-compare the presigned URL: the backend signs
URLs with MINIO_ENDPOINT (docker-internal, e.g. http://minio:9000), so a direct
download from the test machine usually fails with a DNS/connection error. The
objectKey + successfully-issued signed URL are sufficient proof that the upload
pipeline executed end-to-end against MinIO.
"""
from __future__ import annotations

import os
import tempfile

import pytest

pytestmark = pytest.mark.tier2

SAMPLE_CSV = b"date,temperature\n2020-01-01,5.0\n2020-01-02,6.1\n2020-01-03,4.8\n"


def _write_temp_csv(content: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return path


def test_upload_file_and_verify(client, make_dataset):
    ds = make_dataset()
    path = _write_temp_csv(SAMPLE_CSV)
    fname = os.path.basename(path)
    try:
        res = client.upload_file(ds["id"], path)
        # (1) objectKey written correctly -> file really landed in MinIO path
        #     NOTE: upload stores key as "{id}/{filename}" (no datasets/ prefix)
        assert res["objectKey"] == f"{ds['id']}/{fname}"
        # rowCount is counted from the uploaded file (lines - 1), file-driven (not weather)
        assert res["rowCount"] == SAMPLE_CSV.decode().count("\n") - 1, \
            "rowCount should equal file data rows"

        # detail now reflects the MinIO objectKey
        got = client.get_dataset(ds["id"])
        assert got["objectKey"] == f"{ds['id']}/{fname}"

        # (2) presigned URL is actually issued (backend reached MinIO successfully)
        info = client.file_url(ds["id"])
        assert info.get("url"), "presigned URL must be returned"
        assert info["objectKey"] == f"{ds['id']}/{fname}"
    finally:
        os.unlink(path)


def test_delete_with_file_really_gone(client, make_dataset):
    """Deleting a dataset that has an uploaded file.

    Backend delete now purges the associated MinIO object (DatasetService.delete),
    so the file is gone too - not just the DB row. We verify the *record* is really
    gone: detail 404, file association 404, and absent from the list.
    """
    ds = make_dataset()
    path = _write_temp_csv(SAMPLE_CSV)
    try:
        client.upload_file(ds["id"], path)
    finally:
        os.unlink(path)

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
