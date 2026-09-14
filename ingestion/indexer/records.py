"""Gold rows grouped into the documents and chunks the retrieval store holds."""

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Mirrors the CHECK constraint on rag.documents.source_type.
SOURCE_TYPES = frozenset({"pdf", "docx", "wiki", "ticket", "chat", "email"})

_CHUNK_METADATA = (
    "key",
    "heading",
    "title",
    "source_type",
    "source_uri",
    "owner",
    "doc_version",
    "source_updated_at",
    "pii_types",
    "parser_version",
    "chunker_version",
)


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    ordinal: int
    content: str
    content_hash: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DocumentRecord:
    doc_id: str
    source_type: str | None
    source_uri: str
    title: str
    allowed_groups: tuple[str, ...]
    doc_version: str
    source_updated_at: datetime | None
    chunks: tuple[ChunkRecord, ...]

    @property
    def content_hash(self) -> str:
        joined = "\n".join(chunk.content_hash for chunk in self.chunks)
        return hashlib.sha256(joined.encode()).hexdigest()

    def skip_reason(self) -> str | None:
        """Why this document must not be indexed, or None. Skipping fails closed."""
        if not self.allowed_groups:
            return "no allowed_groups"
        if self.source_type not in SOURCE_TYPES:
            return f"unsupported source_type {self.source_type!r}"
        if not self.chunks:
            return "no chunks"
        return None


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) and value else None
    except ValueError:
        return None


def _jsonable(value: Any) -> Any:
    return list(value) if isinstance(value, tuple | list) else value


def group_documents(rows: Iterable[dict[str, Any]]) -> dict[str, DocumentRecord]:
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_doc[row["doc_id"]].append(row)

    documents = {}
    for doc_id, items in by_doc.items():
        items.sort(key=lambda row: row["ordinal"])
        first = items[0]
        documents[doc_id] = DocumentRecord(
            doc_id=doc_id,
            source_type=first["source_type"],
            source_uri=first["source_uri"],
            title=first["title"] or doc_id,
            allowed_groups=tuple(sorted(first["allowed_groups"] or [])),
            doc_version=first["doc_version"],
            source_updated_at=_timestamp(first["source_updated_at"]),
            chunks=tuple(
                ChunkRecord(
                    chunk_id=row["chunk_id"],
                    ordinal=row["ordinal"],
                    content=row["content"],
                    content_hash=row["content_hash"],
                    metadata={name: _jsonable(row.get(name)) for name in _CHUNK_METADATA},
                )
                for row in items
            ),
        )
    return documents
