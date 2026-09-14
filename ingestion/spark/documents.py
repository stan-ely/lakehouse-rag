"""Silver's parse stage: bronze object rows -> parsed documents, inside `mapInPandas`.

Plain pandas around the pure-Python parsers, so it is unit-tested without a JVM and runs the
same on Databricks. A bad file never fails the batch: it becomes a row with `parse_error`, which
keeps the rest of the corpus flowing and makes failures queryable.
"""

from collections.abc import Iterable, Iterator
from typing import Any

import pandas as pd

from ingestion.parsers import PARSER_VERSION, ParseError, parse
from ingestion.pii import detect_pii

PARSED_COLUMNS = [
    "key",
    "bucket",
    "event_id",
    "version_id",
    "sort_key",
    "is_deleted",
    "doc_id",
    "source_type",
    "title",
    "owner",
    "allowed_groups",
    "source_updated_at",
    "content_sha256",
    "fingerprint",
    "sections",
    "text",
    "pii_types",
    "parser_version",
    "parse_error",
]


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _groups(value: Any) -> list[str]:
    # Arrow hands arrays over as numpy arrays; missing ACL metadata means nobody (fail closed).
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    return sorted({str(g) for g in value if g})


def parse_row(row: Any) -> dict[str, Any]:
    key = row.key
    base: dict[str, Any] = {
        "key": key,
        "bucket": row.bucket,
        "event_id": row.event_id,
        "version_id": _str(row.version_id),
        "sort_key": row.sort_key,
        "is_deleted": row.event_type == "delete",
        # S3 metadata is the source system's word on identity, title and access.
        "doc_id": _str(row.doc_id) or key,
        "source_type": _str(row.source_type),
        "title": _str(row.title),
        "owner": _str(row.owner),
        "allowed_groups": _groups(row.allowed_groups),
        "source_updated_at": _str(row.source_updated_at),
        "content_sha256": _str(row.content_sha256),
        "fingerprint": None,
        "sections": [],
        "text": None,
        "pii_types": [],
        "parser_version": PARSER_VERSION,
        "parse_error": None,
    }
    if base["is_deleted"]:
        return base
    if _str(row.fetch_error):
        return {**base, "parse_error": f"fetch: {row.fetch_error}"}
    if row.content is None:
        return {**base, "parse_error": "fetch: no content"}

    try:
        parsed = parse(key, bytes(row.content))
    except ParseError as exc:
        return {**base, "parse_error": f"parse: {exc}"}

    text = parsed.text
    return {
        **base,
        "title": base["title"] or parsed.title or key.rsplit("/", 1)[-1],
        "fingerprint": parsed.fingerprint,
        "sections": [{"heading": s.heading, "text": s.text} for s in parsed.sections],
        "text": text,
        "pii_types": detect_pii(text),
    }


def parse_documents(frames: Iterable[pd.DataFrame]) -> Iterator[pd.DataFrame]:
    for frame in frames:
        rows = [parse_row(row) for row in frame.itertuples(index=False)]
        yield pd.DataFrame(rows, columns=PARSED_COLUMNS)
