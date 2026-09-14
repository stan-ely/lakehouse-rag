"""Gold chunk stage over silver-shaped rows."""

from typing import Any

import numpy as np
import pandas as pd

from ingestion.chunking import CHUNKER_VERSION
from ingestion.spark.chunks import CHUNK_COLUMNS, chunk_rows
from ingestion.spark.schemas import CHUNKS_DDL, column_names


def _silver(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": "wiki/returns.md",
        "bucket": "raw",
        "doc_id": "wiki-returns",
        "version_id": "v1",
        "content_sha256": "sha",
        "title": "Returns Process",
        "source_type": "wiki",
        "owner": "Support",
        # Arrow delivers ARRAY<STRUCT> as numpy arrays of dicts.
        "allowed_groups": np.array(["support", "ops"]),
        "source_updated_at": "2026-06-01T00:00:00+00:00",
        "sections": np.array(
            [
                {"heading": "Steps", "text": "Call support at 303-555-0142 to open a return."},
                {"heading": "Refunds", "text": "Refunds post within 5 days."},
            ]
        ),
        "parser_version": "1",
    }
    return {**row, **overrides}


def _chunks(*rows: dict[str, Any]) -> list[dict[str, Any]]:
    frames = list(chunk_rows([pd.DataFrame(list(rows))]))
    return [r for frame in frames for r in frame.to_dict("records")]


def test_chunks_carry_lineage_acl_and_per_chunk_pii() -> None:
    steps, refunds = _chunks(_silver())

    assert steps["chunk_id"] == "wiki-returns:0000"
    assert refunds["chunk_id"] == "wiki-returns:0001"
    assert steps["content"].startswith("Returns Process > Steps\n\n")
    assert steps["source_uri"] == "s3://raw/wiki/returns.md"
    assert steps["allowed_groups"] == ["ops", "support"]
    assert steps["doc_version"] == "v1"
    assert steps["pii_types"] == ["phone"]
    assert refunds["pii_types"] == []
    assert steps["chunker_version"] == CHUNKER_VERSION


def test_title_change_changes_content_hash_so_embeddings_are_not_reused() -> None:
    before, _ = _chunks(_silver())
    after, _ = _chunks(_silver(title="Returns & Refunds"))

    assert before["content_hash"] != after["content_hash"]


def test_acl_change_keeps_content_hash_but_changes_row_hash() -> None:
    before, _ = _chunks(_silver())
    after, _ = _chunks(_silver(allowed_groups=np.array(["support"])))

    assert before["content_hash"] == after["content_hash"]
    assert before["row_hash"] != after["row_hash"]


def test_identical_input_is_stable() -> None:
    assert _chunks(_silver()) == _chunks(_silver())


def test_unversioned_object_uses_content_sha_as_doc_version() -> None:
    steps, _ = _chunks(_silver(version_id=None))
    assert steps["doc_version"] == "sha"


def test_document_without_sections_yields_no_chunks_but_keeps_schema() -> None:
    (frame,) = list(chunk_rows([pd.DataFrame([_silver(sections=np.array([]))])]))
    assert frame.empty
    assert list(frame.columns) == CHUNK_COLUMNS


def test_output_columns_match_the_gold_contract() -> None:
    assert column_names(CHUNKS_DDL) == CHUNK_COLUMNS
