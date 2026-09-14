"""Reconciles the manifest directory with the raw bucket's current state.

Events can be missed: the bucket held objects before the notification existed, a queue was
purged, or messages expired on the DLQ. Backfill lists every key's latest version (or delete
marker) and writes the same manifests the Lambda would have. Manifest keys are idempotent, so
running it over already-registered objects is a no-op for bronze.

    mise run backfill
"""

import argparse
import logging
import os
from typing import TYPE_CHECKING

from ingestion.lambda_register.handler import (
    build_manifest,
    head,
    manifest_from_head,
    s3_client,
    utc_timestamp,
    write_manifest,
)

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

log = logging.getLogger(__name__)


def backfill(s3: "S3Client", raw_bucket: str, lake_bucket: str, prefix: str = "manifests") -> int:
    written = 0
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=raw_bucket):
        for version in page.get("Versions", []):
            if not version.get("IsLatest"):
                continue
            key = version["Key"]
            # Listings omit user metadata, which carries the ACL, so each object needs a HEAD.
            object_head = head(s3, raw_bucket, key, version.get("VersionId"))
            if object_head is None:
                continue
            write_manifest(
                s3, lake_bucket, manifest_from_head(raw_bucket, key, object_head), prefix
            )
            written += 1
        for marker in page.get("DeleteMarkers", []):
            if not marker.get("IsLatest"):
                continue
            manifest = build_manifest(
                event_name="ObjectRemoved:DeleteMarkerCreated",
                bucket=raw_bucket,
                key=marker["Key"],
                event_time=utc_timestamp(marker["LastModified"]),
                version_id=marker.get("VersionId"),
            )
            write_manifest(s3, lake_bucket, manifest, prefix)
            written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--raw-bucket", default=os.environ.get("RAG_RAW_BUCKET", "larkspur-local-raw")
    )
    parser.add_argument(
        "--lake-bucket", default=os.environ.get("RAG_LAKE_BUCKET", "larkspur-local-lake")
    )
    parser.add_argument("--prefix", default="manifests")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    count = backfill(s3_client(), args.raw_bucket, args.lake_bucket, args.prefix)
    log.info("wrote %d manifests from s3://%s", count, args.raw_bucket)


if __name__ == "__main__":
    main()
