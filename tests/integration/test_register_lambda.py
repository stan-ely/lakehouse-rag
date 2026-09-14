"""End to end on Floci: raw upload -> S3 event -> SQS -> register Lambda -> lake manifest.

Supersedes polling the ingest queue directly: the Lambda's event source mapping now consumes
it, so the observable contract is the manifest it writes.
"""

import json
import os
import time
import uuid
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from botocore.config import Config

from ingestion.lambda_register.handler import event_id

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

pytestmark = pytest.mark.integration

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
RAW_BUCKET = os.environ.get("RAG_RAW_BUCKET", "larkspur-local-raw")
LAKE_BUCKET = os.environ.get("RAG_LAKE_BUCKET", "larkspur-local-lake")
DLQ = os.environ.get("RAG_INGEST_DLQ", "larkspur-local-ingest-dlq")
# Covers a Lambda container cold start on Floci (~30s on a laptop).
TIMEOUT_S = 120


@pytest.fixture(scope="module")
def s3() -> "S3Client":
    return boto3.client("s3", endpoint_url=ENDPOINT, config=Config(s3={"addressing_style": "path"}))


def _wait_for_manifest(s3: "S3Client", eid: str) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_S
    paginator = s3.get_paginator("list_objects_v2")
    while time.monotonic() < deadline:
        for page in paginator.paginate(Bucket=LAKE_BUCKET, Prefix="manifests/"):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(f"/{eid}.json"):
                    body = s3.get_object(Bucket=LAKE_BUCKET, Key=obj["Key"])["Body"].read()
                    manifest: dict[str, Any] = json.loads(body)
                    return manifest
        time.sleep(2)
    pytest.fail(f"no manifest {eid} in s3://{LAKE_BUCKET}/manifests within {TIMEOUT_S}s")


def test_upload_and_delete_are_registered_as_manifests(s3: "S3Client") -> None:
    # A space exercises URL-decoding of keys in S3 event payloads.
    key = f"smoke/register check {uuid.uuid4()}.md"
    put = s3.put_object(
        Bucket=RAW_BUCKET,
        Key=key,
        Body=b"# Smoke\n\n## Body\n\nhello larkspur",
        ContentType="text/markdown",
        Metadata={"doc-id": "smoke", "allowed-groups": "ops,support"},
    )

    upsert = _wait_for_manifest(s3, event_id(RAW_BUCKET, key, put["VersionId"], "upsert"))
    assert upsert["key"] == key
    assert upsert["event_type"] == "upsert"
    assert upsert["allowed_groups"] == ["ops", "support"]
    assert upsert["etag"] == put["ETag"].strip('"')

    marker = s3.delete_object(Bucket=RAW_BUCKET, Key=key)
    delete = _wait_for_manifest(s3, event_id(RAW_BUCKET, key, marker["VersionId"], "delete"))
    assert delete["event_type"] == "delete"
    assert delete["sort_key"] > upsert["sort_key"]


def test_register_leaves_nothing_on_the_dead_letter_queue() -> None:
    sqs = boto3.client("sqs", endpoint_url=ENDPOINT)
    url = sqs.get_queue_url(QueueName=DLQ)["QueueUrl"]
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"])
    assert attrs["Attributes"]["ApproximateNumberOfMessages"] == "0"
