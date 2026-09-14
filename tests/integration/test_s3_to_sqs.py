"""Verifies the raw bucket -> ingest queue event wiring provisioned by `mise run tf-local`."""

import json
import os
import time
import uuid

import boto3
import pytest
from botocore.config import Config

pytestmark = pytest.mark.integration

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
RAW_BUCKET = os.environ.get("RAG_RAW_BUCKET", "larkspur-local-raw")
INGEST_QUEUE = os.environ.get("RAG_INGEST_QUEUE", "larkspur-local-ingest")


def test_upload_to_raw_bucket_emits_ingest_event() -> None:
    s3 = boto3.client("s3", endpoint_url=ENDPOINT, config=Config(s3={"addressing_style": "path"}))
    sqs = boto3.client("sqs", endpoint_url=ENDPOINT)
    queue_url = sqs.get_queue_url(QueueName=INGEST_QUEUE)["QueueUrl"]

    key = f"smoke/{uuid.uuid4()}.txt"
    s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=b"hello larkspur")

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=2)
        for msg in resp.get("Messages", []):
            body = json.loads(msg["Body"])
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
            # s3:TestEvent messages carry no Records.
            keys = {r["s3"]["object"]["key"] for r in body.get("Records", [])}
            if key in keys:
                return

    pytest.fail(f"no S3 event for {key} arrived on {INGEST_QUEUE} within 30s")
