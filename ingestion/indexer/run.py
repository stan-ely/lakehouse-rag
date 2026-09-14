"""Incremental indexer: apply gold's changes since the last run to pgvector.

    mise run index

1. Compare gold's current Delta version with the watermark in `rag.index_state`.
2. Read the change feed in between for the affected doc ids (or reconcile everything on the
   first run, or when that history has been vacuumed).
3. For each batch of documents: reuse stored embeddings with the same content hash, embed the
   rest, and replace the documents in one transaction.
4. Advance the watermark.

Documents are replaced whole, so a replayed batch converges to the same state.
"""

import itertools
import logging
import os
from dataclasses import asdict, dataclass, field

import psycopg

from ingestion.indexer.embed import Embedder, FastEmbedder
from ingestion.indexer.records import group_documents
from ingestion.indexer.source import ChunkSource, GoldSource
from ingestion.indexer.store import PostgresStore, Store

GOLD_SOURCE = "gold.chunks"
log = logging.getLogger(__name__)


@dataclass
class IndexStats:
    table_version: int
    mode: str  # "noop" | "incremental" | "full"
    documents_indexed: int = 0
    documents_removed: int = 0
    chunks_written: int = 0
    embeddings_computed: int = 0
    embeddings_reused: int = 0
    skipped: dict[str, str] = field(default_factory=dict)


def index(
    source: ChunkSource, store: Store, embedder: Embedder, batch_size: int = 64
) -> IndexStats:
    version = source.version()
    since = store.watermark(GOLD_SOURCE)
    if since is not None and since >= version:
        return IndexStats(version, "noop")

    changed = None if since is None else source.changed_doc_ids(since, version)
    stats = IndexStats(version, "full" if changed is None else "incremental")
    documents = group_documents(source.chunk_rows(changed, version))
    affected = sorted(documents) if changed is None else sorted(changed)

    for batch in itertools.batched(affected, batch_size):
        indexable = []
        for doc_id in batch:
            doc = documents.get(doc_id)
            if doc is None:
                stats.documents_removed += 1
            elif reason := doc.skip_reason():
                stats.skipped[doc_id] = reason
            else:
                indexable.append(doc)

        texts = {chunk.content_hash: chunk.content for doc in indexable for chunk in doc.chunks}
        vectors = store.embeddings_for(embedder.model_name, texts)
        missing = [content_hash for content_hash in texts if content_hash not in vectors]
        computed = embedder.embed([texts[content_hash] for content_hash in missing])
        vectors.update(zip(missing, computed, strict=True))

        store.replace_documents(batch, indexable, vectors, embedder.model_name)
        stats.embeddings_reused += len(texts) - len(missing)
        stats.embeddings_computed += len(missing)
        stats.documents_indexed += len(indexable)
        stats.chunks_written += sum(len(doc.chunks) for doc in indexable)

    if changed is None:
        keep = [doc_id for doc_id in documents if doc_id not in stats.skipped]
        stats.documents_removed += store.delete_documents_except(keep)
    for doc_id, reason in sorted(stats.skipped.items()):
        log.warning("not indexed %s: %s", doc_id, reason)

    store.set_watermark(GOLD_SOURCE, version)
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # The model download logs every HTTP request at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    dsn = os.environ.get("DATABASE_URL", "postgresql://rag:rag@localhost:5432/rag")
    with psycopg.connect(dsn.replace("+psycopg", ""), autocommit=True) as conn:
        stats = index(GoldSource.from_env(), PostgresStore(conn), FastEmbedder())
    log.info("index %s", asdict(stats))


if __name__ == "__main__":
    main()
