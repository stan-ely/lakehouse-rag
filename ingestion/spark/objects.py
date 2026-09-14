"""Fetches the exact object version a manifest names, for bronze to store alongside it.

Runs inside `mapInPandas` on executors, so it is plain pandas + boto3 and unit-testable without
a JVM. Reading by version id (not by path) matters: if a page is edited twice before ingest runs,
bronze must record each version's own bytes rather than the latest bytes twice.
"""

import hashlib
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

import pandas as pd
from botocore.exceptions import ClientError

from ingestion.lambda_register.handler import s3_client

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

# Keeps one oversized upload from exhausting the ~1.5 GB ingest container.
MAX_OBJECT_BYTES = 25 * 1024 * 1024
_MISSING = {"404", "NoSuchKey", "NoSuchVersion"}


def _present(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def fetch_object(
    s3: "S3Client", event_type: str, bucket: str, key: str, version_id: Any, size: Any
) -> tuple[bytes | None, str | None]:
    """Returns (content, error). Permanent problems are recorded; transient ones raise."""
    if event_type == "delete":
        return None, None
    if isinstance(size, int | float) and not pd.isna(size) and size > MAX_OBJECT_BYTES:
        return None, f"too_large: {int(size)} bytes"
    kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if _present(version_id):
        kwargs["VersionId"] = version_id
    try:
        return s3.get_object(**kwargs)["Body"].read(), None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _MISSING:
            # The version was purged; a later manifest describes what replaced it.
            return None, f"missing: {code}"
        raise


def fetch_contents(
    frames: Iterable[pd.DataFrame], s3: "S3Client | None" = None
) -> Iterator[pd.DataFrame]:
    client = s3 or s3_client()
    for frame in frames:
        contents: list[bytes | None] = []
        hashes: list[str | None] = []
        errors: list[str | None] = []
        for row in frame.itertuples(index=False):
            content, error = fetch_object(
                client, row.event_type, row.bucket, row.key, row.version_id, row.size
            )
            contents.append(content)
            hashes.append(hashlib.sha256(content).hexdigest() if content is not None else None)
            errors.append(error)
        yield frame.assign(content=contents, content_sha256=hashes, fetch_error=errors)
