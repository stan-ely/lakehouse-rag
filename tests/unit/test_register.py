"""Register Lambda and backfill against an in-memory S3 double."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from botocore.exceptions import ClientError

from ingestion.lambda_register.backfill import backfill
from ingestion.lambda_register.handler import event_id, handler, sort_key, utc_timestamp

LAKE = "lake"
RAW = "raw"
MODIFIED = datetime(2026, 9, 14, 10, 0, 0, 123000, tzinfo=UTC)


class FakeS3:
    """Just enough S3 for the register code: versioned HEAD, PUT and version listings."""

    def __init__(self) -> None:
        self.versions: dict[tuple[str, str], dict[str, Any]] = {}
        self.current: dict[str, str | None] = {}  # key -> latest version id (None = delete marker)
        self.puts: dict[str, bytes] = {}
        self.fail_head_for: set[str] = set()

    def add(self, key: str, version: str, metadata: dict[str, str] | None = None) -> None:
        self.versions[(key, version)] = {
            "VersionId": version,
            "ETag": f'"etag-{version}"',
            "ContentLength": 42,
            "ContentType": "text/markdown",
            "LastModified": MODIFIED,
            "Metadata": metadata or {},
        }
        self.current[key] = version

    def head_object(self, Bucket: str, Key: str, VersionId: str | None = None) -> dict[str, Any]:
        if Key in self.fail_head_for:
            raise ClientError({"Error": {"Code": "500", "Message": "boom"}}, "HeadObject")
        version = VersionId or self.current.get(Key)
        if version is None or (Key, version) not in self.versions:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return self.versions[(Key, version)]

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self.puts[Key] = Body

    def get_paginator(self, _name: str) -> "FakeS3":
        return self

    def paginate(self, Bucket: str) -> list[dict[str, Any]]:
        versions = [
            {"Key": k, "VersionId": v, "IsLatest": self.current.get(k) == v}
            for k, v in self.versions
        ]
        markers = [
            {"Key": k, "VersionId": "dm", "IsLatest": True, "LastModified": MODIFIED}
            for k, v in self.current.items()
            if v is None
        ]
        return [{"Versions": versions, "DeleteMarkers": markers}]

    def manifests(self) -> list[dict[str, Any]]:
        return [json.loads(body) for body in self.puts.values()]


def _record(key: str, event_name: str = "ObjectCreated:Put", version: str = "v1") -> dict[str, Any]:
    return {
        "eventName": event_name,
        "eventTime": "2026-09-14T10:00:01.123456789Z",
        "s3": {
            "bucket": {"name": RAW},
            "object": {"key": key, "versionId": version, "eTag": "abc", "size": 42},
        },
    }


def _sqs_event(*bodies: dict[str, Any]) -> dict[str, Any]:
    return {
        "Records": [{"messageId": f"m{i}", "body": json.dumps(b)} for i, b in enumerate(bodies)]
    }


@pytest.fixture(autouse=True)
def _lake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAKE_BUCKET", LAKE)


def test_upsert_manifest_carries_acl_from_object_metadata() -> None:
    s3 = FakeS3()
    s3.add(
        "wiki/pto policy.md",
        "v1",
        {"doc-id": "wiki-pto", "allowed-groups": "hr, all-staff,hr", "title": "PTO"},
    )

    result = handler(
        _sqs_event({"Records": [_record("wiki/pto+policy.md")]}),
        None,
        s3,  # type: ignore[arg-type]
    )

    assert result == {"batchItemFailures": []}
    (manifest,) = s3.manifests()
    assert manifest["key"] == "wiki/pto policy.md"  # URL-decoded
    assert manifest["event_type"] == "upsert"
    assert manifest["doc_id"] == "wiki-pto"
    assert manifest["allowed_groups"] == ["all-staff", "hr"]
    assert manifest["etag"] == "abc"
    expected_id = event_id(RAW, "wiki/pto policy.md", "v1", "upsert")
    assert list(s3.puts) == [f"manifests/dt=2026-09-14/{expected_id}.json"]


def test_redelivery_rewrites_the_same_manifest_key() -> None:
    s3 = FakeS3()
    s3.add("wiki/a.md", "v1")
    event = _sqs_event({"Records": [_record("wiki/a.md")]})

    handler(event, None, s3)  # type: ignore[arg-type]
    handler(event, None, s3)  # type: ignore[arg-type]

    assert len(s3.puts) == 1


def test_missing_allowed_groups_fails_closed() -> None:
    s3 = FakeS3()
    s3.add("wiki/a.md", "v1", {"doc-id": "a"})

    handler(_sqs_event({"Records": [_record("wiki/a.md")]}), None, s3)  # type: ignore[arg-type]

    assert s3.manifests()[0]["allowed_groups"] == []


def test_delete_marker_needs_no_head() -> None:
    s3 = FakeS3()
    record = _record("wiki/gone.md", "ObjectRemoved:DeleteMarkerCreated", "dm1")

    handler(_sqs_event({"Records": [record]}), None, s3)  # type: ignore[arg-type]

    (manifest,) = s3.manifests()
    assert manifest["event_type"] == "delete"
    assert manifest["allowed_groups"] == []


def test_purging_an_old_version_reregisters_the_current_one() -> None:
    s3 = FakeS3()
    s3.add("wiki/a.md", "v2", {"doc-id": "a", "allowed-groups": "ops"})

    record = _record("wiki/a.md", "ObjectRemoved:Delete", "v1")
    handler(_sqs_event({"Records": [record]}), None, s3)  # type: ignore[arg-type]

    (manifest,) = s3.manifests()
    assert manifest["event_type"] == "upsert"
    assert manifest["version_id"] == "v2"


def test_vanished_object_is_skipped_not_failed() -> None:
    s3 = FakeS3()

    result = handler(_sqs_event({"Records": [_record("wiki/a.md")]}), None, s3)  # type: ignore[arg-type]

    assert result == {"batchItemFailures": []}
    assert s3.puts == {}


def test_only_failed_messages_are_reported_for_retry() -> None:
    s3 = FakeS3()
    s3.add("wiki/ok.md", "v1")
    s3.add("wiki/bad.md", "v1")
    s3.fail_head_for.add("wiki/bad.md")
    event = _sqs_event(
        {"Records": [_record("wiki/ok.md")]},
        {"Records": [_record("wiki/bad.md")]},
        {"Service": "Amazon S3", "Event": "s3:TestEvent"},
    )

    result = handler(event, None, s3)  # type: ignore[arg-type]

    assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    assert [m["key"] for m in s3.manifests()] == ["wiki/ok.md"]


def test_backfill_registers_latest_versions_and_delete_markers() -> None:
    s3 = FakeS3()
    s3.add("wiki/a.md", "v1")
    s3.add("wiki/a.md", "v2", {"allowed-groups": "ops"})
    s3.add("wiki/b.md", "v1")
    s3.current["wiki/b.md"] = None

    assert backfill(s3, RAW, LAKE) == 2  # type: ignore[arg-type]

    by_key = {m["key"]: m for m in s3.manifests()}
    assert by_key["wiki/a.md"]["version_id"] == "v2"
    assert by_key["wiki/a.md"]["allowed_groups"] == ["ops"]
    assert by_key["wiki/b.md"]["event_type"] == "delete"


def test_backfill_and_lambda_agree_on_the_manifest_key() -> None:
    s3 = FakeS3()
    s3.add("wiki/a.md", "v1")
    backfill(s3, RAW, LAKE)  # type: ignore[arg-type]
    handler(_sqs_event({"Records": [_record("wiki/a.md")]}), None, s3)  # type: ignore[arg-type]

    assert len(s3.puts) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-14T10:00:01.123456789Z", "2026-09-14T10:00:01.123456789Z"),
        ("2026-09-14T10:00:01.12Z", "2026-09-14T10:00:01.120000000Z"),
        ("2026-09-14T10:00:01Z", "2026-09-14T10:00:01.000000000Z"),
        ("2026-09-14T10:00:01.5+00:00", "2026-09-14T10:00:01.500000000Z"),
        (MODIFIED, "2026-09-14T10:00:00.123000000Z"),
    ],
)
def test_utc_timestamp_is_fixed_width(value: str | datetime, expected: str) -> None:
    assert utc_timestamp(value) == expected


def test_sort_key_orders_by_sequencer_then_time() -> None:
    assert sort_key("0A", "z") < sort_key("0055AB", "a")
    assert sort_key(None, "2026-09-14T10:00:01Z") < sort_key(None, "2026-09-14T10:00:01.5Z")
