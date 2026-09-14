"""Register Lambda: turns raw-bucket S3 events (via SQS) into bronze manifest records.

The function does no parsing. It records *that* an object version changed, plus the S3 user
metadata that carries the document's ACL, as one JSON manifest in the lake bucket. Bronze then
streams the manifest directory, so ingestion is incremental and replayable, and a slow or broken
parser can never back up the queue.

Guarantees:
- Idempotent: the manifest key is derived from (bucket, key, version, event type), so SQS
  redelivery or a backfill rewrites the same object instead of adding a duplicate.
- Ordered per key downstream: `sort_key` uses the S3 sequencer when present (AWS), otherwise the
  event time (Floci emits none). Bronze keeps the highest `sort_key` for each object key.
- Partial failures: only the SQS messages that failed are retried (ReportBatchItemFailures);
  after `maxReceiveCount` attempts they land on the DLQ.
- Fail closed on ACLs: an object without `allowed-groups` metadata gets an empty group list,
  which no retriever can read.

This module deliberately depends only on the standard library and boto3 (bundled in the Lambda
runtime), so the deployment package is just this file.
"""

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote_plus

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

MANIFEST_VERSION = 1
log = logging.getLogger()
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@dataclass(frozen=True)
class Manifest:
    manifest_version: int
    event_id: str
    event_type: str  # "upsert" | "delete"
    event_name: str
    bucket: str
    key: str
    version_id: str | None
    etag: str | None
    size: int | None
    event_time: str
    sequencer: str | None
    sort_key: str
    content_type: str | None
    doc_id: str | None
    source_type: str | None
    title: str | None
    owner: str | None
    allowed_groups: list[str]
    source_updated_at: str | None
    metadata: dict[str, str]
    registered_at: str

    @property
    def object_key(self) -> str:
        return manifest_key(self.event_id, self.event_time)


def manifest_key(event_id: str, event_time: str, prefix: str = "manifests") -> str:
    return f"{prefix}/dt={event_time[:10]}/{event_id}.json"


def event_id(bucket: str, key: str, version: str, event_type: str) -> str:
    return hashlib.sha256(f"{bucket}\n{key}\n{version}\n{event_type}".encode()).hexdigest()


def utc_timestamp(value: str | datetime) -> str:
    """Fixed-width `YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ`, so timestamps compare correctly as strings.

    Floci emits nanosecond event times, AWS milliseconds, and backfill reads `LastModified`
    datetimes; mixing those formats would make string ordering wrong within the same second.
    """
    if isinstance(value, datetime):
        value = value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")
    base, _, fraction = value.removesuffix("Z").removesuffix("+00:00").partition(".")
    return f"{base}.{fraction.ljust(9, '0')[:9]}Z"


def sort_key(sequencer: str | None, event_time: str) -> str:
    # Sequencers are hex strings of varying length: only comparable once left-padded.
    return sequencer.rjust(32, "0") if sequencer else utc_timestamp(event_time)


def build_manifest(
    *,
    event_name: str,
    bucket: str,
    key: str,
    event_time: str,
    version_id: str | None = None,
    etag: str | None = None,
    size: int | None = None,
    sequencer: str | None = None,
    head: dict[str, Any] | None = None,
) -> Manifest:
    event_type = "delete" if event_name.startswith("ObjectRemoved") else "upsert"
    metadata = {k.lower(): v for k, v in (head or {}).get("Metadata", {}).items()}
    groups = [g.strip() for g in metadata.get("allowed-groups", "").split(",") if g.strip()]
    version = version_id or sequencer or event_time
    eid = event_id(bucket, key, version, event_type)
    return Manifest(
        manifest_version=MANIFEST_VERSION,
        event_id=eid,
        event_type=event_type,
        event_name=event_name,
        bucket=bucket,
        key=key,
        version_id=version_id,
        etag=etag.strip('"') if etag else None,
        size=size,
        event_time=event_time,
        sequencer=sequencer,
        sort_key=sort_key(sequencer, event_time),
        content_type=(head or {}).get("ContentType"),
        doc_id=metadata.get("doc-id"),
        source_type=metadata.get("source-type"),
        title=metadata.get("title"),
        owner=metadata.get("owner"),
        allowed_groups=sorted(set(groups)),
        source_updated_at=metadata.get("updated-at"),
        metadata=metadata,
        registered_at=datetime.now(UTC).isoformat(),
    )


def s3_client() -> "S3Client":
    # Floci injects AWS_ENDPOINT_URL; its hostname cannot resolve virtual-hosted bucket names.
    emulated = bool(os.environ.get("AWS_ENDPOINT_URL"))
    config = Config(s3={"addressing_style": "path" if emulated else "auto"})
    return boto3.client("s3", config=config)


def head(s3: "S3Client", bucket: str, key: str, version_id: str | None) -> dict[str, Any] | None:
    """Object metadata for the event's version, or None if that version no longer exists."""
    kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if version_id:
        kwargs["VersionId"] = version_id
    try:
        return dict(s3.head_object(**kwargs))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NoSuchVersion"}:
            return None
        raise


def write_manifest(s3: "S3Client", lake_bucket: str, manifest: Manifest, prefix: str) -> str:
    key = manifest_key(manifest.event_id, manifest.event_time, prefix)
    s3.put_object(
        Bucket=lake_bucket,
        Key=key,
        Body=json.dumps(asdict(manifest), sort_keys=True).encode(),
        ContentType="application/json",
    )
    return key


def register_record(
    s3: "S3Client", record: dict[str, Any], lake_bucket: str, prefix: str
) -> str | None:
    """Writes the manifest for one S3 event record; returns its key, or None if skipped."""
    bucket = record["s3"]["bucket"]["name"]
    obj = record["s3"]["object"]
    # S3 URL-encodes keys in event payloads (spaces arrive as '+').
    key = unquote_plus(obj["key"])
    event_name = record["eventName"]
    version_id = obj.get("versionId")

    object_head = None
    if event_name.startswith("ObjectCreated"):
        object_head = head(s3, bucket, key, version_id)
        if object_head is None:
            # Overwritten or deleted before we got here; that later event supersedes this one.
            log.info("skip vanished object %s version=%s", key, version_id)
            return None
    elif event_name == "ObjectRemoved:Delete" and version_id:
        # A specific version was purged. If the object still exists, the document did not go
        # away: its current version may have changed, so re-register that instead.
        current = head(s3, bucket, key, None)
        if current is not None:
            return write_manifest(s3, lake_bucket, manifest_from_head(bucket, key, current), prefix)

    manifest = build_manifest(
        event_name=event_name,
        bucket=bucket,
        key=key,
        event_time=record["eventTime"],
        version_id=version_id,
        etag=obj.get("eTag"),
        size=obj.get("size"),
        sequencer=obj.get("sequencer"),
        head=object_head,
    )
    return write_manifest(s3, lake_bucket, manifest, prefix)


def manifest_from_head(bucket: str, key: str, object_head: dict[str, Any]) -> Manifest:
    """Upsert manifest for an object's current version, as seen by HeadObject or a listing."""
    return build_manifest(
        event_name="ObjectCreated:Put",
        bucket=bucket,
        key=key,
        event_time=utc_timestamp(object_head["LastModified"]),
        version_id=object_head.get("VersionId"),
        etag=object_head.get("ETag"),
        size=object_head.get("ContentLength"),
        head=object_head,
    )


def handler(event: dict[str, Any], _context: Any, s3: "S3Client | None" = None) -> dict[str, Any]:
    s3 = s3 or s3_client()
    lake_bucket = os.environ["LAKE_BUCKET"]
    prefix = os.environ.get("MANIFEST_PREFIX", "manifests")
    failures = []
    for message in event.get("Records", []):
        message_id = message["messageId"]
        try:
            body = json.loads(message["body"])
            # s3:TestEvent (sent when a notification is configured) has no Records.
            for record in body.get("Records", []):
                written = register_record(s3, record, lake_bucket, prefix)
                if written:
                    log.info("registered %s -> %s", record["s3"]["object"]["key"], written)
        except Exception:
            log.exception("failed to register message %s", message_id)
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}
