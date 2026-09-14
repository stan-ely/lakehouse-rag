"""Runs the bronze job in the ingest container and checks the Delta table it writes.

Expects `mise run seed` and `mise run backfill` first, so every raw object has a manifest.
"""

import hashlib
import json
from typing import TYPE_CHECKING, Any

import pytest

from tests.integration.lake import LAKE_BUCKET, run_ingest, s3_client, table_rows

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def s3() -> "S3Client":
    return s3_client()


def _manifest_event_ids(s3: "S3Client") -> set[str]:
    paginator = s3.get_paginator("list_objects_v2")
    return {
        obj["Key"].rsplit("/", 1)[-1].removesuffix(".json")
        for page in paginator.paginate(Bucket=LAKE_BUCKET, Prefix="manifests/")
        for obj in page.get("Contents", [])
    }


@pytest.fixture(scope="module")
def first_run(s3: "S3Client") -> tuple[set[str], list[dict[str, Any]]]:
    registered = _manifest_event_ids(s3)
    assert registered, "no manifests: run `mise run seed` and `mise run backfill` first"
    run_ingest("bronze")
    return registered, table_rows("bronze", "objects")


def test_bronze_holds_every_registered_event_once(
    first_run: tuple[set[str], list[dict[str, Any]]],
) -> None:
    registered, rows = first_run
    event_ids = [row["event_id"] for row in rows]

    assert len(event_ids) == len(set(event_ids))
    assert registered <= set(event_ids)


def test_bronze_stores_the_exact_object_version(
    s3: "S3Client", first_run: tuple[set[str], list[dict[str, Any]]]
) -> None:
    _, rows = first_run
    upserts = [r for r in rows if r["event_type"] == "upsert" and r["fetch_error"] is None]
    assert upserts

    for row in upserts:
        assert hashlib.sha256(row["content"]).hexdigest() == row["content_sha256"]
    sample = next(r for r in upserts if r["key"].endswith(".json"))
    original = s3.get_object(
        Bucket=sample["bucket"], Key=sample["key"], VersionId=sample["version_id"]
    )
    assert original["Body"].read() == sample["content"]
    assert json.loads(sample["content"])["messages"]


def test_deletes_carry_no_content(first_run: tuple[set[str], list[dict[str, Any]]]) -> None:
    _, rows = first_run
    assert all(r["content"] is None for r in rows if r["event_type"] == "delete")


def test_rerun_is_idempotent(first_run: tuple[set[str], list[dict[str, Any]]]) -> None:
    _, rows = first_run
    run_ingest("bronze")

    assert len(table_rows("bronze", "objects")) == len(rows)
