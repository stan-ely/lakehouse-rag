"""Builders for query-side test data."""

from typing import Any

from app.retrieval.hybrid import RetrievedChunk


def make_chunk(
    n: int = 1,
    *,
    content: str | None = None,
    similarity: float = 0.8,
    lexical_rank: int | None = 1,
    groups: tuple[str, ...] = ("all-staff",),
    **overrides: Any,
) -> RetrievedChunk:
    values: dict[str, Any] = {
        "chunk_id": f"doc-{n}:0000",
        "doc_id": f"doc-{n}",
        "ordinal": 0,
        "content": content or f"Policy {n} > Section\n\nRefunds post within {n} days.",
        "title": f"Policy {n}",
        "source_type": "wiki",
        "source_uri": f"s3://raw/wiki/doc-{n}.md",
        "doc_version": "v1",
        "score": 1 / (60 + n),
        "similarity": similarity,
        "vector_rank": n,
        "lexical_rank": lexical_rank,
        "metadata": {"source_updated_at": "2026-06-01T00:00:00+00:00"},
        "allowed_groups": groups,
    }
    return RetrievedChunk(**{**values, **overrides})
