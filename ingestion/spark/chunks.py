"""Gold's chunk stage: live, canonical silver documents -> retrieval chunks, in `mapInPandas`.

Two hashes serve the indexer:
- `content_hash` is sha256 of exactly the text that gets embedded (title/heading header
  included), so an embedding can be reused whenever it matches, and never when it does not.
- `row_hash` also covers everything stored next to the vector (ACL, title, version, PII tags),
  so gold rewrites a chunk, and emits a change-feed row, only when something indexed changed.
"""

import hashlib
import json
from collections.abc import Iterable, Iterator
from typing import Any

import pandas as pd

from ingestion.chunking import CHUNKER_VERSION, chunk_document
from ingestion.parsers import ParsedDocument, Section
from ingestion.pii import detect_pii

CHUNK_COLUMNS = [
    "chunk_id",
    "doc_id",
    "key",
    "ordinal",
    "heading",
    "content",
    "content_hash",
    "row_hash",
    "title",
    "source_type",
    "source_uri",
    "owner",
    "allowed_groups",
    "source_updated_at",
    "doc_version",
    "pii_types",
    "parser_version",
    "chunker_version",
]

_HASHED = [c for c in CHUNK_COLUMNS if c not in {"row_hash"}]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _list(value: Any) -> list[Any]:
    # Arrow hands ARRAY columns to pandas as numpy arrays (of dicts, for structs).
    return [] if value is None else list(value)


def chunk_row(doc: Any) -> list[dict[str, Any]]:
    sections = tuple(Section(s["heading"], s["text"]) for s in _list(doc.sections))
    if not sections:
        return []
    parsed = ParsedDocument(doc.title, sections)
    groups = sorted(str(g) for g in _list(doc.allowed_groups))
    rows = []
    for chunk in chunk_document(parsed):
        content = chunk.content(doc.title)
        row = {
            "chunk_id": f"{doc.doc_id}:{chunk.ordinal:04d}",
            "doc_id": doc.doc_id,
            "key": doc.key,
            "ordinal": chunk.ordinal,
            "heading": chunk.heading,
            "content": content,
            "content_hash": _sha256(content),
            "title": doc.title,
            "source_type": doc.source_type,
            "source_uri": f"s3://{doc.bucket}/{doc.key}",
            "owner": doc.owner,
            "allowed_groups": groups,
            "source_updated_at": doc.source_updated_at,
            "doc_version": doc.version_id or doc.content_sha256,
            "pii_types": detect_pii(chunk.text),
            "parser_version": doc.parser_version,
            "chunker_version": CHUNKER_VERSION,
        }
        row["row_hash"] = _sha256(json.dumps([row[c] for c in _HASHED], default=str))
        rows.append(row)
    return rows


def chunk_rows(frames: Iterable[pd.DataFrame]) -> Iterator[pd.DataFrame]:
    for frame in frames:
        rows = [row for doc in frame.itertuples(index=False) for row in chunk_row(doc)]
        yield pd.DataFrame(rows, columns=CHUNK_COLUMNS)
