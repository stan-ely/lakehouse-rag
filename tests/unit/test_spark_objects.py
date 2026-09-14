"""JVM-free pieces of the Spark jobs: the bronze byte fetcher and the table contracts."""

import hashlib
import io
from dataclasses import fields
from typing import Any

import pandas as pd
import pytest
from botocore.exceptions import ClientError

from ingestion.lambda_register.handler import Manifest
from ingestion.spark.objects import MAX_OBJECT_BYTES, fetch_contents
from ingestion.spark.schemas import BRONZE_OBJECTS_DDL, MANIFEST_DDL, column_names


class FakeS3:
    def __init__(self, objects: dict[tuple[str, str | None], bytes]) -> None:
        self.objects = objects
        self.calls: list[dict[str, Any]] = []
        self.transient_failure = False

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.transient_failure:
            raise ClientError({"Error": {"Code": "SlowDown", "Message": "x"}}, "GetObject")
        body = self.objects.get((kwargs["Key"], kwargs.get("VersionId")))
        if body is None:
            raise ClientError({"Error": {"Code": "NoSuchVersion", "Message": "x"}}, "GetObject")
        return {"Body": io.BytesIO(body)}


def _frame(*rows: dict[str, Any]) -> pd.DataFrame:
    base = {"event_type": "upsert", "bucket": "raw", "version_id": "v1", "size": 3}
    return pd.DataFrame([{**base, **row} for row in rows])


def _fetch(s3: FakeS3, frame: pd.DataFrame) -> pd.DataFrame:
    return pd.concat(list(fetch_contents([frame], s3)))  # type: ignore[arg-type]


def test_fetches_the_manifest_version_not_the_latest() -> None:
    s3 = FakeS3({("a.md", "v1"): b"old", ("a.md", "v2"): b"new"})

    out = _fetch(s3, _frame({"key": "a.md", "version_id": "v1"}))

    assert out.loc[0, "content"] == b"old"
    assert out.loc[0, "content_sha256"] == hashlib.sha256(b"old").hexdigest()
    assert s3.calls == [{"Bucket": "raw", "Key": "a.md", "VersionId": "v1"}]


def test_unversioned_manifest_reads_current_object() -> None:
    s3 = FakeS3({("a.md", None): b"x"})

    out = _fetch(s3, _frame({"key": "a.md", "version_id": None}))

    assert out.loc[0, "content"] == b"x"


def test_deletes_and_oversized_objects_are_not_downloaded() -> None:
    s3 = FakeS3({})
    frame = _frame(
        {"key": "gone.md", "event_type": "delete", "size": None},
        {"key": "huge.pdf", "size": MAX_OBJECT_BYTES + 1},
    )

    out = _fetch(s3, frame)

    assert s3.calls == []
    assert out["content"].isna().all()
    # pandas 3 stores missing strings as NaN; Arrow turns them into nulls for Spark.
    assert pd.isna(out.loc[0, "fetch_error"])
    assert str(out.loc[1, "fetch_error"]).startswith("too_large")


def test_purged_version_is_recorded_not_raised() -> None:
    out = _fetch(FakeS3({}), _frame({"key": "a.md"}))

    assert out.loc[0, "fetch_error"] == "missing: NoSuchVersion"


def test_transient_errors_fail_the_batch_for_retry() -> None:
    s3 = FakeS3({("a.md", "v1"): b"x"})
    s3.transient_failure = True

    with pytest.raises(ClientError):
        _fetch(s3, _frame({"key": "a.md"}))


def test_manifest_ddl_matches_the_lambda_contract() -> None:
    assert column_names(MANIFEST_DDL) == [f.name for f in fields(Manifest)]


def test_bronze_extends_manifest_columns() -> None:
    bronze = column_names(BRONZE_OBJECTS_DDL)
    assert bronze[: len(fields(Manifest))] == [f.name for f in fields(Manifest)]
    assert bronze[-4:] == ["content", "content_sha256", "fetch_error", "ingested_at"]
