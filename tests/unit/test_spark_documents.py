"""Silver parse stage over bronze-shaped rows, using the generator's real renderings."""

import hashlib
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from data_gen.models import SourceDocument, section
from data_gen.render import render
from ingestion.parsers import PARSER_VERSION
from ingestion.spark.documents import PARSED_COLUMNS, parse_documents
from ingestion.spark.schemas import PARSED_DDL, column_names


def _doc(key: str, *paragraphs: str) -> SourceDocument:
    return SourceDocument(
        doc_id="wiki-returns",
        key=key,
        title="Returns Process",
        allowed_groups=("support",),
        owner="Support",
        updated_at=datetime(2026, 6, 1, tzinfo=UTC),
        sections=(section("Steps", *paragraphs),),
    )


def _bronze(**overrides: Any) -> dict[str, Any]:
    content = render(_doc("wiki/returns.md", "Email returns@larkspur.example for a label."))
    row: dict[str, Any] = {
        "key": "wiki/returns.md",
        "bucket": "raw",
        "event_id": "e1",
        "event_type": "upsert",
        "version_id": "v1",
        "sort_key": "2026-09-14T10:00:00.000000000Z",
        "doc_id": "wiki-returns",
        "source_type": "wiki",
        "title": "Returns Process",
        "owner": "Support",
        # Arrow delivers ARRAY<STRING> columns to pandas as numpy arrays.
        "allowed_groups": np.array(["support", "ops"]),
        "source_updated_at": "2026-06-01T00:00:00+00:00",
        "content": content,
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "fetch_error": None,
    }
    return {**row, **overrides}


def _parse(*rows: dict[str, Any]) -> list[dict[str, Any]]:
    frames = list(parse_documents([pd.DataFrame(list(rows))]))
    return [r for frame in frames for r in frame.to_dict("records")]


def test_parsed_document_carries_acl_sections_fingerprint_and_pii() -> None:
    (doc,) = _parse(_bronze())

    assert doc["is_deleted"] is False
    assert doc["parse_error"] is None
    assert doc["allowed_groups"] == ["ops", "support"]
    assert doc["title"] == "Returns Process"
    assert [s["heading"] for s in doc["sections"]] == ["Steps"]
    assert "returns@larkspur.example" in doc["text"]
    assert doc["pii_types"] == ["email"]
    assert len(doc["fingerprint"]) == 64
    assert doc["parser_version"] == PARSER_VERSION


def test_delete_event_is_a_tombstone_without_parsing() -> None:
    (doc,) = _parse(_bronze(event_type="delete", content=None, content_sha256=None))

    assert doc["is_deleted"] is True
    assert doc["parse_error"] is None
    assert doc["text"] is None


def test_fetch_and_parse_failures_become_rows_not_exceptions() -> None:
    fetch_failed, unparseable = _parse(
        _bronze(event_id="e1", content=None, fetch_error="missing: NoSuchVersion"),
        _bronze(event_id="e2", key="smoke/file.txt", content=b"hello"),
    )

    assert fetch_failed["parse_error"] == "fetch: missing: NoSuchVersion"
    assert str(unparseable["parse_error"]).startswith("parse: no parser")


def test_missing_acl_metadata_fails_closed_and_doc_id_falls_back_to_key() -> None:
    (doc,) = _parse(_bronze(allowed_groups=None, doc_id=None, title=None))

    assert doc["allowed_groups"] == []
    assert doc["doc_id"] == "wiki/returns.md"
    assert doc["title"] == "Returns Process"  # recovered from the document itself


def test_output_columns_match_the_silver_contract() -> None:
    assert column_names(PARSED_DDL) == PARSED_COLUMNS
