"""Indexer support: string document versions, embedding reuse, change-feed watermark.

- `rag.documents.doc_version` becomes text: it holds the S3 object version id (or the content
  hash for unversioned sources), which is what a citation should pin to.
- `rag.chunks.content_hash` is sha256 of the exact embedded text. The indexer looks embeddings
  up by (embed_model, content_hash) before calling the model, so re-indexing a document whose
  ACL or neighbours changed costs no inference.
- `rag.index_state` records the last gold Delta version applied, so each run reads only the
  change feed since then. It advances only after every batch has committed; batches replace
  whole documents, so replaying one after a crash is idempotent.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE rag.documents ALTER COLUMN doc_version DROP DEFAULT")
    op.execute(
        "ALTER TABLE rag.documents ALTER COLUMN doc_version TYPE text USING doc_version::text"
    )

    op.execute("ALTER TABLE rag.chunks ADD COLUMN content_hash text")
    op.execute(
        "UPDATE rag.chunks SET content_hash = encode(sha256(convert_to(content, 'UTF8')), 'hex')"
    )
    op.execute("ALTER TABLE rag.chunks ALTER COLUMN content_hash SET NOT NULL")
    op.execute("CREATE INDEX chunks_content_hash_idx ON rag.chunks (embed_model, content_hash)")

    op.execute(
        """
        CREATE TABLE rag.index_state (
            source        text PRIMARY KEY,
            table_version bigint NOT NULL CHECK (table_version >= 0),
            updated_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Not retrieval data: no RLS, and only the indexer may touch it.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON rag.index_state TO rag_indexer")


def downgrade() -> None:
    op.execute("DROP TABLE rag.index_state")
    op.execute("DROP INDEX rag.chunks_content_hash_idx")
    op.execute("ALTER TABLE rag.chunks DROP COLUMN content_hash")
    op.execute(
        "ALTER TABLE rag.documents ALTER COLUMN doc_version TYPE integer USING 1, "
        "ALTER COLUMN doc_version SET DEFAULT 1"
    )
