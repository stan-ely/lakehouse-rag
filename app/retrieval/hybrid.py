"""One SQL round trip: vector and full-text candidates fused with Reciprocal Rank Fusion.

Why RRF: cosine similarities and `ts_rank_cd` scores live on unrelated scales, so adding them
needs per-corpus tuning that silently drifts. RRF only uses each list's *ranks*
(score = sum 1 / (k + rank)), which is robust without calibration.

Access control is enforced twice, independently:
1. every candidate query filters `allowed_groups && caller_groups` explicitly, and
2. the transaction runs as `rag_retriever` with `app.user_groups` set, so row-level security
   hides everything else even if a future query forgets the filter.
An empty group set returns nothing without touching the database.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SEARCH_SQL = """
WITH vector_hits AS (
    SELECT chunk_id,
           1 - (embedding <=> %(embedding)s) AS similarity,
           row_number() OVER (ORDER BY embedding <=> %(embedding)s) AS rank
    FROM rag.chunks
    WHERE allowed_groups && %(groups)s
    ORDER BY embedding <=> %(embedding)s
    LIMIT %(candidates)s
),
lexical_hits AS (
    SELECT chunk_id,
           row_number() OVER (ORDER BY ts_rank_cd(tsv, query) DESC, chunk_id) AS rank
    FROM rag.chunks, websearch_to_tsquery('english', %(query)s) AS query
    WHERE tsv @@ query AND allowed_groups && %(groups)s
    ORDER BY ts_rank_cd(tsv, query) DESC, chunk_id
    LIMIT %(candidates)s
),
fused AS (
    SELECT coalesce(v.chunk_id, l.chunk_id) AS chunk_id,
           coalesce(1.0 / (%(rrf_k)s + v.rank), 0)
             + coalesce(1.0 / (%(rrf_k)s + l.rank), 0) AS score,
           v.rank AS vector_rank,
           l.rank AS lexical_rank,
           v.similarity
    FROM vector_hits v
    FULL OUTER JOIN lexical_hits l ON l.chunk_id = v.chunk_id
)
SELECT c.chunk_id, c.doc_id, c.ordinal, c.content, c.metadata, c.allowed_groups,
       d.title, d.source_type, d.source_uri, d.doc_version, d.source_updated_at,
       f.score, f.vector_rank, f.lexical_rank,
       coalesce(f.similarity, 1 - (c.embedding <=> %(embedding)s)) AS similarity
FROM fused f
JOIN rag.chunks c ON c.chunk_id = f.chunk_id
JOIN rag.documents d ON d.doc_id = c.doc_id
ORDER BY f.score DESC, f.chunk_id
LIMIT %(k)s
"""


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class QueryEmbedder(Protocol):
    def embed_query(self, text: str) -> NDArray[np.float32]: ...


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    ordinal: int
    content: str
    title: str
    source_type: str
    source_uri: str
    doc_version: str
    score: float
    similarity: float
    vector_rank: int | None
    lexical_rank: int | None
    metadata: dict[str, Any]
    allowed_groups: tuple[str, ...]


class HybridRetriever:
    """Needs a pool created with `configure=pgvector.psycopg.register_vector`."""

    def __init__(
        self,
        pool: ConnectionPool[Connection[Any]],
        embedder: QueryEmbedder,
        *,
        candidates: int = 40,
        rrf_k: int = 60,
        ef_search: int = 100,
    ) -> None:
        self.pool = pool
        self.embedder = embedder
        self.candidates = candidates
        self.rrf_k = rrf_k
        self.ef_search = ef_search

    def search(self, query: str, groups: Sequence[str], k: int) -> list[RetrievedChunk]:
        caller_groups = sorted({g for g in groups if g})
        if not caller_groups or not query.strip():
            return []
        embedding = self.embedder.embed_query(query)
        params = {
            "embedding": embedding,
            "groups": caller_groups,
            "query": query,
            "candidates": max(self.candidates, k),
            "rrf_k": self.rrf_k,
            "k": k,
        }
        with (
            self.pool.connection() as conn,
            conn.transaction(),
            conn.cursor(row_factory=dict_row) as cur,
        ):
            cur.execute(
                "SELECT set_config('app.user_groups', %s, true)", (",".join(caller_groups),)
            )
            cur.execute("SET LOCAL ROLE rag_retriever")
            # Filtered HNSW scans can return fewer rows than LIMIT; iterative scans keep going.
            cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            cur.execute(f"SET LOCAL hnsw.ef_search = {int(self.ef_search)}")
            cur.execute(SEARCH_SQL, params)
            rows = cur.fetchall()
        return [
            RetrievedChunk(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                ordinal=row["ordinal"],
                content=row["content"],
                title=row["title"],
                source_type=row["source_type"],
                source_uri=row["source_uri"],
                doc_version=row["doc_version"],
                score=float(row["score"]),
                similarity=float(row["similarity"]),
                vector_rank=row["vector_rank"],
                lexical_rank=row["lexical_rank"],
                metadata={
                    **(row["metadata"] or {}),
                    "source_updated_at": _isoformat(row["source_updated_at"]),
                },
                allowed_groups=tuple(row["allowed_groups"]),
            )
            for row in rows
        ]
