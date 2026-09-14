"""Indexer against real gold and Postgres, including the incremental edit-one-page path."""

import os
import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from ingestion.indexer.embed import FastEmbedder
from ingestion.indexer.records import SOURCE_TYPES
from ingestion.indexer.run import IndexStats, index
from ingestion.indexer.source import GoldSource
from ingestion.indexer.store import PostgresStore
from ingestion.lambda_register.backfill import backfill
from tests.integration.lake import (
    LAKE_BUCKET,
    RAW_BUCKET,
    STORAGE_OPTIONS,
    run_ingest,
    s3_client,
    table_rows,
)

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

pytestmark = pytest.mark.integration

DSN = os.environ.get("DATABASE_URL", "postgresql://rag:rag@localhost:5432/rag")
EMBEDDER = FastEmbedder()


@pytest.fixture(scope="module")
def conn() -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(DSN.replace("+psycopg", ""), autocommit=True) as connection:
        yield connection


def _index(conn: psycopg.Connection[Any]) -> IndexStats:
    source = GoldSource(f"s3://{LAKE_BUCKET}/delta/gold/chunks", STORAGE_OPTIONS)
    return index(source, PostgresStore(conn), EMBEDDER)


def _indexable(chunk: dict[str, Any]) -> bool:
    return bool(chunk["allowed_groups"]) and chunk["source_type"] in SOURCE_TYPES


@pytest.fixture(scope="module")
def settled(conn: psycopg.Connection[Any]) -> IndexStats:
    """Brings lake and index up to date, then returns the stats of a run with nothing to do."""
    run_ingest("bronze", "silver", "gold")
    _index(conn)
    return _index(conn)


def test_run_with_no_gold_changes_is_a_noop(settled: IndexStats) -> None:
    assert settled.mode == "noop"


def test_index_matches_gold(conn: psycopg.Connection[Any], settled: IndexStats) -> None:
    gold = {c["chunk_id"]: c for c in table_rows("gold", "chunks") if _indexable(c)}
    stored = conn.execute(
        "SELECT chunk_id, content_hash, allowed_groups FROM rag.chunks"
    ).fetchall()

    assert {chunk_id for chunk_id, _, _ in stored} == set(gold)
    for chunk_id, content_hash, groups in stored:
        assert content_hash == gold[chunk_id]["content_hash"]
        assert sorted(groups) == sorted(gold[chunk_id]["allowed_groups"])


def test_retriever_sees_exactly_the_chunks_its_groups_allow(
    conn: psycopg.Connection[Any], settled: IndexStats
) -> None:
    groups = {"sales", "all-staff"}
    expected = {
        c["chunk_id"]
        for c in table_rows("gold", "chunks")
        if _indexable(c) and groups & set(c["allowed_groups"])
    }
    with conn.transaction():
        conn.execute("SELECT set_config('app.user_groups', %s, true)", (",".join(groups),))
        conn.execute("SET LOCAL ROLE rag_retriever")
        visible = {row[0] for row in conn.execute("SELECT chunk_id FROM rag.chunks")}

    assert visible == expected
    total = conn.execute("SELECT count(*) FROM rag.chunks").fetchone()
    assert total is not None
    assert len(visible) < total[0], "seed data should include chunks sales cannot see"


def _put(s3: "S3Client", key: str, body: bytes, head: dict[str, Any]) -> None:
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=key,
        Body=body,
        ContentType=head["ContentType"],
        Metadata=head["Metadata"],
    )


def _sync(conn: psycopg.Connection[Any], s3: "S3Client") -> IndexStats:
    # Backfill registers the new version deterministically instead of waiting on the Lambda.
    backfill(s3, RAW_BUCKET, LAKE_BUCKET)
    run_ingest("bronze", "silver", "gold")
    return _index(conn)


def test_editing_one_page_reindexes_only_that_document(
    conn: psycopg.Connection[Any], settled: IndexStats
) -> None:
    s3 = s3_client()
    chunks_per_doc: dict[tuple[str, str], int] = {}
    for chunk in table_rows("gold", "chunks"):
        if chunk["key"].startswith("wiki/") and chunk["key"].endswith(".md") and _indexable(chunk):
            ident = (chunk["doc_id"], chunk["key"])
            chunks_per_doc[ident] = chunks_per_doc.get(ident, 0) + 1
    # A multi-chunk page without copies: an edit then changes one chunk and one document.
    (doc_id, key), _ = max(
        ((ident, n) for ident, n in chunks_per_doc.items() if "escalation-matrix" not in ident[1]),
        key=lambda item: (item[1], item[0]),
    )
    head = dict(s3.head_object(Bucket=RAW_BUCKET, Key=key))
    original = s3.get_object(Bucket=RAW_BUCKET, Key=key)["Body"].read()
    marker = f"Integration marker {uuid.uuid4().hex}."

    def contents() -> list[str]:
        query = "SELECT content FROM rag.chunks WHERE doc_id = %s ORDER BY ordinal"
        return [row[0] for row in conn.execute(query, (doc_id,))]

    try:
        _put(s3, key, original + f"\n{marker}\n".encode(), head)
        edited = _sync(conn, s3)

        assert edited.mode == "incremental"
        assert edited.documents_indexed == 1
        assert edited.embeddings_computed == 1
        assert edited.embeddings_reused == chunks_per_doc[(doc_id, key)] - 1
        assert any(marker in content for content in contents())
    finally:
        _put(s3, key, original, head)
        restored = _sync(conn, s3)

    # Replacing the edited document dropped the original last chunk's vector, so restoring it
    # costs exactly one embedding again; every other chunk is reused.
    assert restored.documents_indexed == 1
    assert restored.embeddings_computed == 1
    assert restored.embeddings_reused == chunks_per_doc[(doc_id, key)] - 1
    assert not any(marker in content for content in contents())
