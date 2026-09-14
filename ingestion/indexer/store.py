"""Writes to the retrieval store as `rag_indexer`, one transaction per batch of documents."""

from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Protocol

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from ingestion.indexer.embed import Vector
from ingestion.indexer.records import DocumentRecord


class Store(Protocol):
    def watermark(self, source: str) -> int | None: ...

    def embeddings_for(self, model: str, hashes: Collection[str]) -> dict[str, Vector]: ...

    def replace_documents(
        self,
        doc_ids: Sequence[str],
        documents: Sequence[DocumentRecord],
        vectors: Mapping[str, Vector],
        model: str,
    ) -> None: ...

    def delete_documents_except(self, keep: Collection[str]) -> int: ...

    def set_watermark(self, source: str, version: int) -> None: ...


class PostgresStore:
    """Requires an autocommit connection, so each `transaction()` is a real BEGIN/COMMIT."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        if not conn.autocommit:
            raise ValueError("PostgresStore needs an autocommit connection")
        self.conn = conn
        register_vector(conn)

    @contextmanager
    def _as_indexer(self) -> Iterator[psycopg.Cursor[Any]]:
        with self.conn.transaction(), self.conn.cursor() as cur:
            # Row-level security is forced on rag.*: act through the indexer's policy, never as
            # the table owner, so the same code works with a least-privilege login role.
            cur.execute("SET LOCAL ROLE rag_indexer")
            yield cur

    def watermark(self, source: str) -> int | None:
        with self._as_indexer() as cur:
            cur.execute("SELECT table_version FROM rag.index_state WHERE source = %s", (source,))
            row = cur.fetchone()
        return None if row is None else int(row[0])

    def embeddings_for(self, model: str, hashes: Collection[str]) -> dict[str, Vector]:
        if not hashes:
            return {}
        with self._as_indexer() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (content_hash) content_hash, embedding
                FROM rag.chunks
                WHERE embed_model = %s AND content_hash = ANY(%s)
                """,
                (model, list(hashes)),
            )
            rows = cur.fetchall()
        # register_vector loads `vector` columns as pgvector.Vector objects.
        return {
            content_hash: np.asarray(vector.to_numpy(), dtype=np.float32)
            for content_hash, vector in rows
        }

    def replace_documents(
        self,
        doc_ids: Sequence[str],
        documents: Sequence[DocumentRecord],
        vectors: Mapping[str, Vector],
        model: str,
    ) -> None:
        with self._as_indexer() as cur:
            # Chunks cascade. Deleting every affected id also removes documents that were
            # deleted upstream or are no longer indexable.
            cur.execute("DELETE FROM rag.documents WHERE doc_id = ANY(%s)", (list(doc_ids),))
            cur.executemany(
                """
                INSERT INTO rag.documents (doc_id, source_type, source_uri, title, allowed_groups,
                                           content_hash, doc_version, source_updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        doc.doc_id,
                        doc.source_type,
                        doc.source_uri,
                        doc.title,
                        list(doc.allowed_groups),
                        doc.content_hash,
                        doc.doc_version,
                        doc.source_updated_at,
                    )
                    for doc in documents
                ],
            )
            cur.executemany(
                """
                INSERT INTO rag.chunks (chunk_id, doc_id, ordinal, content, content_hash,
                                        embedding, embed_model, allowed_groups, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        chunk.chunk_id,
                        doc.doc_id,
                        chunk.ordinal,
                        chunk.content,
                        chunk.content_hash,
                        vectors[chunk.content_hash],
                        model,
                        list(doc.allowed_groups),
                        Jsonb(chunk.metadata),
                    )
                    for doc in documents
                    for chunk in doc.chunks
                ],
            )

    def delete_documents_except(self, keep: Collection[str]) -> int:
        with self._as_indexer() as cur:
            cur.execute("DELETE FROM rag.documents WHERE NOT (doc_id = ANY(%s))", (list(keep),))
            return cur.rowcount

    def set_watermark(self, source: str, version: int) -> None:
        with self._as_indexer() as cur:
            cur.execute(
                """
                INSERT INTO rag.index_state (source, table_version) VALUES (%s, %s)
                ON CONFLICT (source) DO UPDATE
                SET table_version = excluded.table_version, updated_at = now()
                """,
                (source, version),
            )
