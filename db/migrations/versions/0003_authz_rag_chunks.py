"""Retrieval store: documents and chunks with hybrid-search indexes and row-level security.

Access model (fail closed):
- The API sets `app.user_groups` (comma-separated, transaction-local) from the verified JWT
  and runs `SET LOCAL ROLE rag_retriever`. RLS then only returns rows whose `allowed_groups`
  overlap the caller's groups; an unset setting yields no rows.
- The indexer runs as `rag_indexer`, which may read and write every row.
- Both roles are NOLOGIN. Login roles are created per environment (credentials live in
  Secrets Manager) and granted membership, so no secret is ever stored in a migration.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 384  # BAAI/bge-small-en-v1.5; changing models requires a re-embed migration.
ROLES = ("rag_retriever", "rag_indexer")


def _create_role(name: str) -> None:
    # Roles are cluster-wide, so tolerate them surviving a downgrade in another database.
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{name}') THEN
                CREATE ROLE {name} NOLOGIN;
            END IF;
        END $$
        """
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA authz")
    op.execute(
        """
        CREATE FUNCTION authz.current_groups() RETURNS text[]
        LANGUAGE sql STABLE PARALLEL SAFE AS $$
            SELECT coalesce(
                string_to_array(nullif(current_setting('app.user_groups', true), ''), ','),
                '{}'::text[]
            )
        $$
        """
    )

    op.execute("CREATE SCHEMA rag")
    op.execute(
        """
        CREATE TABLE rag.documents (
            doc_id            text PRIMARY KEY,
            source_type       text NOT NULL CHECK (
                                  source_type IN ('pdf', 'docx', 'wiki', 'ticket', 'chat', 'email')
                              ),
            source_uri        text NOT NULL,
            title             text NOT NULL,
            allowed_groups    text[] NOT NULL CHECK (cardinality(allowed_groups) > 0),
            content_hash      text NOT NULL,
            doc_version       integer NOT NULL DEFAULT 1,
            source_updated_at timestamptz,
            indexed_at        timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE rag.chunks (
            chunk_id       text PRIMARY KEY,
            doc_id         text NOT NULL REFERENCES rag.documents (doc_id) ON DELETE CASCADE,
            ordinal        integer NOT NULL,
            content        text NOT NULL,
            tsv            tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            embedding      vector({EMBEDDING_DIM}) NOT NULL,
            embed_model    text NOT NULL,
            allowed_groups text[] NOT NULL CHECK (cardinality(allowed_groups) > 0),
            metadata       jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (doc_id, ordinal)
        )
        """
    )
    op.execute(
        "CREATE INDEX chunks_embedding_idx ON rag.chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("CREATE INDEX chunks_tsv_idx ON rag.chunks USING gin (tsv)")
    op.execute("CREATE INDEX chunks_groups_idx ON rag.chunks USING gin (allowed_groups)")
    op.execute("CREATE INDEX documents_groups_idx ON rag.documents USING gin (allowed_groups)")

    for role in ROLES:
        _create_role(role)
    op.execute("GRANT USAGE ON SCHEMA authz, rag TO rag_retriever, rag_indexer")
    op.execute("GRANT EXECUTE ON FUNCTION authz.current_groups() TO rag_retriever, rag_indexer")
    op.execute("GRANT SELECT ON rag.documents, rag.chunks TO rag_retriever")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON rag.documents, rag.chunks TO rag_indexer")

    for table in ("documents", "chunks"):
        op.execute(f"ALTER TABLE rag.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE rag.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_retriever_read ON rag.{table}
            FOR SELECT TO rag_retriever
            USING (allowed_groups && authz.current_groups())
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_indexer_all ON rag.{table}
            FOR ALL TO rag_indexer
            USING (true) WITH CHECK (true)
            """
        )


def downgrade() -> None:
    op.execute("DROP SCHEMA rag CASCADE")
    op.execute("DROP SCHEMA authz CASCADE")
    for role in ROLES:
        op.execute(f"DROP OWNED BY {role}")
        op.execute(f"DROP ROLE IF EXISTS {role}")
