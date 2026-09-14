"""Shared access to the local lake (Floci S3 + Delta tables) for integration tests."""

import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from botocore.config import Config
from deltalake import DeltaTable

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
RAW_BUCKET = os.environ.get("RAG_RAW_BUCKET", "larkspur-local-raw")
LAKE_BUCKET = os.environ.get("RAG_LAKE_BUCKET", "larkspur-local-lake")
STORAGE_OPTIONS = {
    "AWS_ENDPOINT_URL": ENDPOINT,
    "AWS_ALLOW_HTTP": "true",
    "AWS_REGION": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    "AWS_ACCESS_KEY_ID": os.environ.get("AWS_ACCESS_KEY_ID", "test"),
    "AWS_SECRET_ACCESS_KEY": os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
}


def s3_client() -> "S3Client":
    return boto3.client("s3", endpoint_url=ENDPOINT, config=Config(s3={"addressing_style": "path"}))


def delta_table(layer: str, name: str) -> DeltaTable:
    uri = f"s3://{LAKE_BUCKET}/delta/{layer}/{name}"
    return DeltaTable(uri, storage_options=STORAGE_OPTIONS)


def table_rows(layer: str, name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = delta_table(layer, name).to_pyarrow_table().to_pylist()
    return rows


def run_ingest(*steps: str) -> None:
    """Runs medallion steps in the one-shot ingest container, as `mise run ingest` does."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker CLI not found")
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        [docker, "compose", "--profile", "ingest", "run", "--rm", "spark", *steps],
        check=True,
        timeout=1200,
    )
