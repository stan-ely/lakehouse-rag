"""Indexer orchestration against in-memory source, store and embedder."""

from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pytest

from ingestion.indexer.embed import Vector
from ingestion.indexer.records import DocumentRecord, group_documents
from ingestion.indexer.run import GOLD_SOURCE, index


def _chunk(doc_id: str, ordinal: int, content: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "chunk_id": f"{doc_id}:{ordinal:04d}",
        "doc_id": doc_id,
        "key": f"wiki/{doc_id}.md",
        "ordinal": ordinal,
        "heading": "Body",
        "content": content,
        "content_hash": f"h-{content}",
        "title": doc_id.title(),
        "source_type": "wiki",
        "source_uri": f"s3://raw/wiki/{doc_id}.md",
        "owner": "Ops",
        "allowed_groups": ["ops"],
        "source_updated_at": "2026-06-01T00:00:00+00:00",
        "doc_version": "v1",
        "pii_types": [],
        "parser_version": "1",
        "chunker_version": "1",
    }
    return {**row, **overrides}


class FakeSource:
    def __init__(self, rows: list[dict[str, Any]], version: int = 3) -> None:
        self.rows = rows
        self.current = version
        self.changes: dict[int, set[str]] = {}
        self.history_available = True

    def version(self) -> int:
        return self.current

    def changed_doc_ids(self, after_version: int, up_to_version: int) -> set[str] | None:
        if not self.history_available:
            return None
        return {
            doc_id
            for version, ids in self.changes.items()
            if after_version < version <= up_to_version
            for doc_id in ids
        }

    def chunk_rows(self, doc_ids: Collection[str] | None, version: int) -> list[dict[str, Any]]:
        return [r for r in self.rows if doc_ids is None or r["doc_id"] in doc_ids]


class FakeStore:
    def __init__(self) -> None:
        self.documents: dict[str, DocumentRecord] = {}
        self.vectors: dict[str, Vector] = {}
        self.watermarks: dict[str, int] = {}

    def watermark(self, source: str) -> int | None:
        return self.watermarks.get(source)

    def embeddings_for(self, model: str, hashes: Collection[str]) -> dict[str, Vector]:
        return {h: self.vectors[h] for h in hashes if h in self.vectors}

    def replace_documents(
        self,
        doc_ids: Sequence[str],
        documents: Sequence[DocumentRecord],
        vectors: Mapping[str, Vector],
        model: str,
    ) -> None:
        for doc_id in doc_ids:
            self.documents.pop(doc_id, None)
        for doc in documents:
            self.documents[doc.doc_id] = doc
            for chunk in doc.chunks:
                self.vectors[chunk.content_hash] = vectors[chunk.content_hash]

    def delete_documents_except(self, keep: Collection[str]) -> int:
        stale = [doc_id for doc_id in self.documents if doc_id not in keep]
        for doc_id in stale:
            del self.documents[doc_id]
        return len(stale)

    def set_watermark(self, source: str, version: int) -> None:
        self.watermarks[source] = version


class FakeEmbedder:
    model_name = "fake"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        self.calls.append(list(texts))
        return [np.full(384, len(text), dtype=np.float32) for text in texts]


@pytest.fixture
def rows() -> list[dict[str, Any]]:
    return [
        _chunk("a", 1, "second"),
        _chunk("a", 0, "first"),
        _chunk("b", 0, "shared"),
        _chunk("c", 0, "shared"),  # same content as b: embedded once
        _chunk("secret", 0, "no acl", allowed_groups=[]),
        _chunk("odd", 0, "txt", source_type="txt"),
    ]


def test_first_run_indexes_everything_and_fails_closed_on_bad_documents(
    rows: list[dict[str, Any]],
) -> None:
    store, embedder = FakeStore(), FakeEmbedder()

    stats = index(FakeSource(rows), store, embedder)

    assert stats.mode == "full"
    assert sorted(store.documents) == ["a", "b", "c"]
    assert stats.skipped == {
        "secret": "no allowed_groups",
        "odd": "unsupported source_type 'txt'",
    }
    assert stats.embeddings_computed == 3  # first, second, shared
    assert store.watermarks == {GOLD_SOURCE: 3}
    assert [c.ordinal for c in store.documents["a"].chunks] == [0, 1]


def test_rerun_at_the_same_version_is_a_noop(rows: list[dict[str, Any]]) -> None:
    source, store, embedder = FakeSource(rows), FakeStore(), FakeEmbedder()
    index(source, store, embedder)
    embedder.calls.clear()

    stats = index(source, store, embedder)

    assert stats.mode == "noop"
    assert embedder.calls == []


def test_incremental_run_touches_only_changed_documents_and_reuses_embeddings(
    rows: list[dict[str, Any]],
) -> None:
    source, store, embedder = FakeSource(rows), FakeStore(), FakeEmbedder()
    index(source, store, embedder)
    embedder.calls.clear()

    # Version 4: doc a gains a chunk and changes ACL; doc b is deleted upstream.
    source.rows = [r for r in rows if r["doc_id"] not in {"a", "b"}] + [
        _chunk("a", 0, "first", allowed_groups=["ops", "sales"]),
        _chunk("a", 1, "second", allowed_groups=["ops", "sales"]),
        _chunk("a", 2, "third", allowed_groups=["ops", "sales"]),
    ]
    source.current = 4
    source.changes = {4: {"a", "b"}}

    stats = index(source, store, embedder)

    assert stats.mode == "incremental"
    assert embedder.calls == [["third"]]
    assert stats.embeddings_reused == 2
    assert stats.documents_removed == 1
    assert sorted(store.documents) == ["a", "c"]
    assert store.documents["a"].allowed_groups == ("ops", "sales")


def test_vacuumed_history_falls_back_to_full_reconcile(rows: list[dict[str, Any]]) -> None:
    source, store, embedder = FakeSource(rows), FakeStore(), FakeEmbedder()
    index(source, store, embedder)
    source.rows = [r for r in rows if r["doc_id"] != "c"]
    source.current = 9
    source.history_available = False

    stats = index(source, store, embedder)

    assert stats.mode == "full"
    assert "c" not in store.documents
    assert stats.documents_removed == 1


def test_group_documents_orders_chunks_and_hashes_the_document() -> None:
    docs = group_documents([_chunk("a", 1, "y"), _chunk("a", 0, "x")])

    doc = docs["a"]
    assert [c.content for c in doc.chunks] == ["x", "y"]
    assert doc.source_updated_at == datetime(2026, 6, 1, tzinfo=UTC)
    assert doc.chunks[0].metadata["key"] == "wiki/a.md"
    assert (
        doc.content_hash
        == group_documents([_chunk("a", 0, "x"), _chunk("a", 1, "y")])["a"].content_hash
    )
