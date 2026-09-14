"""Writes the generated dataset locally, syncs documents to S3 and loads the ops database."""

import hashlib
import json
import logging
import shutil
from dataclasses import astuple, dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg import sql

from data_gen.models import SourceDocument
from data_gen.world import World

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "json": "application/json",
}
# The generator owns these prefixes: objects under them that it no longer produces are deleted,
# which exercises the delete path of the ingestion pipeline.
MANAGED_PREFIXES = ("documents/", "wiki/", "tickets/", "chat/", "email/")


@dataclass(frozen=True)
class RenderedDocument:
    doc: SourceDocument
    data: bytes

    @property
    def md5(self) -> str:
        return hashlib.md5(self.data, usedforsecurity=False).hexdigest()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def content_type(self) -> str:
        return CONTENT_TYPES[self.doc.key.rsplit(".", 1)[-1]]

    @property
    def metadata(self) -> dict[str, str]:
        """S3 user metadata: the ingestion pipeline's source of truth for ACLs."""
        return {
            "doc-id": self.doc.doc_id,
            "source-type": self.doc.source_type,
            "title": self.doc.title,
            "owner": self.doc.owner,
            "allowed-groups": ",".join(self.doc.allowed_groups),
            "updated-at": self.doc.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class SyncStats:
    uploaded: int
    unchanged: int
    deleted: int


def write_local(rendered: list[RenderedDocument], out_dir: Path) -> Path:
    raw = out_dir / "raw"
    shutil.rmtree(raw, ignore_errors=True)
    for item in rendered:
        path = raw / item.doc.key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(item.data)
    manifest = [{"key": item.doc.key, "sha256": item.sha256, **item.metadata} for item in rendered]
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def sync_to_s3(client: "S3Client", bucket: str, rendered: list[RenderedDocument]) -> SyncStats:
    existing: dict[str, str] = {}
    paginator = client.get_paginator("list_objects_v2")
    for prefix in MANAGED_PREFIXES:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                existing[obj["Key"]] = obj["ETag"].strip('"')

    uploaded = unchanged = 0
    for item in rendered:
        key = item.doc.key
        if existing.get(key) == item.md5:
            head = client.head_object(Bucket=bucket, Key=key)
            if head.get("Metadata", {}) == item.metadata:
                unchanged += 1
                continue
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=item.data,
            ContentType=item.content_type,
            Metadata=item.metadata,
        )
        uploaded += 1

    stale = sorted(set(existing) - {item.doc.key for item in rendered})
    for start in range(0, len(stale), 1000):
        batch = stale[start : start + 1000]
        client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in batch]})
    return SyncStats(uploaded=uploaded, unchanged=unchanged, deleted=len(stale))


def load_ops(world: World, dsn: str) -> dict[str, int]:
    """Replaces the ops tables with the generated world in a single transaction."""
    tables: list[tuple[str, tuple[Any, ...]]] = [
        ("employees", world.employees),
        ("employee_compensation", world.compensation),
        ("customers", world.customers),
        ("shipments", world.shipments),
        ("invoices", world.invoices),
    ]
    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("TRUNCATE {}").format(
                sql.SQL(", ").join(sql.Identifier("ops", name) for name, _ in reversed(tables))
            )
        )
        for name, rows in tables:
            columns = [f.name for f in fields(rows[0])]
            statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier("ops", name), sql.SQL(", ").join(map(sql.Identifier, columns))
            )
            with cur.copy(statement) as copy:
                for row in rows:
                    copy.write_row(astuple(row))
            counts[name] = len(rows)
    return counts
