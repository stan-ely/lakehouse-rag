"""Fetches the exact object version a manifest names, for bronze to store alongside it.

Runs inside `mapInPandas` on executors, so it is plain pandas + boto3 and unit-testable without
a JVM. Reading by version id (not by path) matters: if a page is edited twice before ingest runs,
bronze must record each version's own bytes rather than the latest bytes twice.

Credentials: locally and in the Lambda the default chain applies. On Databricks, a Unity
Catalog external location gives Spark access to the lake but gives boto3 nothing, so the job
names a UC *service credential*; the driver resolves it once per batch and the executors get
the resulting short-lived keys (see `service_credentials`).
"""

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import boto3
import pandas as pd
from botocore.exceptions import ClientError

from ingestion.lambda_register.handler import s3_client

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

# Keeps one oversized upload from exhausting the ~1.5 GB ingest container.
MAX_OBJECT_BYTES = 25 * 1024 * 1024
_MISSING = {"404", "NoSuchKey", "NoSuchVersion"}


@dataclass(frozen=True)
class Credentials:
    """Short-lived AWS keys handed from the driver to executors. Never logged or persisted."""

    access_key: str
    secret_key: str
    token: str | None
    region: str

    def __repr__(self) -> str:
        return f"Credentials(access_key={self.access_key[:4]}..., region={self.region})"


def service_credentials(name: str, region: str) -> Credentials:
    """Resolves a Unity Catalog service credential on the Databricks driver.

    `dbutils` exists only on Databricks, hence the local import. The keys are temporary (about
    an hour), which is why bronze resolves them per micro-batch rather than once per job.
    """
    from databricks.sdk.runtime import dbutils  # type: ignore[import-not-found,unused-ignore]

    # The SDK's local dbutils stub predates service credentials; the runtime object has them.
    provider = cast(Any, dbutils).credentials.getServiceCredentialsProvider(name)
    resolved = boto3.Session(botocore_session=provider).get_credentials()
    if resolved is None:
        raise RuntimeError(f"service credential {name!r} resolved no AWS keys")
    frozen = resolved.get_frozen_credentials()
    return Credentials(str(frozen.access_key), str(frozen.secret_key), frozen.token, region)


def raw_client(credentials: Credentials | None = None) -> "S3Client":
    if credentials is None:
        return s3_client()
    session = boto3.Session(
        aws_access_key_id=credentials.access_key,
        aws_secret_access_key=credentials.secret_key,
        aws_session_token=credentials.token,
        region_name=credentials.region,
    )
    return session.client("s3")


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
    frames: Iterable[pd.DataFrame],
    s3: "S3Client | None" = None,
    credentials: Credentials | None = None,
) -> Iterator[pd.DataFrame]:
    client = s3 or raw_client(credentials)
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
