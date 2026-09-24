"""Where the lake lives. Locally that is Floci over S3A; on Databricks, an external location.

Jobs only ever see paths from here, so the same code runs in the local container and in the
Databricks Asset Bundle job; the difference is configuration.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    lake_root: str
    # Set only for S3-compatible emulators; on AWS and Databricks the SDK default endpoint applies.
    s3_endpoint: str | None = None
    region: str = "us-east-1"
    # Databricks only: the UC service credential bronze uses to read raw objects with boto3.
    service_credential: str | None = None
    shuffle_partitions: int = 4
    manifest_files_per_batch: int = 500

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            lake_root=os.environ.get("RAG_LAKE_ROOT", "s3a://larkspur-local-lake").rstrip("/"),
            s3_endpoint=os.environ.get("RAG_S3A_ENDPOINT") or None,
            region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
            service_credential=os.environ.get("RAG_SERVICE_CREDENTIAL") or None,
            shuffle_partitions=int(os.environ.get("RAG_SHUFFLE_PARTITIONS", "4")),
        )

    @property
    def manifests_path(self) -> str:
        return f"{self.lake_root}/manifests"

    def table_path(self, layer: str, name: str) -> str:
        return f"{self.lake_root}/delta/{layer}/{name}"

    def checkpoint_path(self, name: str) -> str:
        return f"{self.lake_root}/_checkpoints/{name}"
