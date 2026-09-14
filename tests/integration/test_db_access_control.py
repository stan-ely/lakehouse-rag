"""Proves the database-layer access controls hold independently of application code.

Each test runs in a transaction that is rolled back, so it leaves no data behind.
"""

import os
from collections.abc import Iterator

import psycopg
import pytest

pytestmark = pytest.mark.integration

DEFAULT_URL = "postgresql+psycopg://rag:rag@localhost:5432/rag"
DSN = os.environ.get("DATABASE_URL", DEFAULT_URL).replace("+psycopg", "")
ZERO_VECTOR = "[" + ",".join(["0"] * 384) + "]"


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    with psycopg.connect(DSN) as connection:
        yield connection
        connection.rollback()


def _act_as(conn: psycopg.Connection, role: str, groups: list[str] | None) -> None:
    conn.execute("RESET ROLE")
    if groups is None:
        conn.execute("RESET app.user_groups")
    else:
        conn.execute("SELECT set_config('app.user_groups', %s, true)", (",".join(groups),))
    conn.execute(f"SET LOCAL ROLE {role}")


def _seed_chunks(conn: psycopg.Connection) -> None:
    _act_as(conn, "rag_indexer", None)
    for doc_id, groups in (("doc-public", ["all-staff"]), ("doc-hr", ["hr", "exec"])):
        conn.execute(
            """
            INSERT INTO rag.documents (doc_id, source_type, source_uri, title, allowed_groups,
                                       content_hash)
            VALUES (%s, 'pdf', %s, %s, %s, 'hash')
            """,
            (doc_id, f"s3://raw/{doc_id}.pdf", doc_id, groups),
        )
        conn.execute(
            """
            INSERT INTO rag.chunks (chunk_id, doc_id, ordinal, content, embedding, embed_model,
                                    allowed_groups)
            VALUES (%s, %s, 0, 'salary bands and travel policy', %s::vector, 'test', %s)
            """,
            (f"{doc_id}#0", doc_id, ZERO_VECTOR, groups),
        )


def _visible_chunks(conn: psycopg.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT chunk_id FROM rag.chunks").fetchall()}


@pytest.mark.parametrize(
    ("groups", "expected"),
    [
        (["all-staff", "sales"], {"doc-public#0"}),
        (["all-staff", "hr"], {"doc-public#0", "doc-hr#0"}),
        ([], set()),
        (None, set()),  # fail closed when the API forgets to set groups
    ],
)
def test_retriever_only_sees_chunks_for_its_groups(
    conn: psycopg.Connection, groups: list[str] | None, expected: set[str]
) -> None:
    _seed_chunks(conn)
    _act_as(conn, "rag_retriever", groups)
    assert _visible_chunks(conn) == expected


def test_retriever_cannot_write_chunks(conn: psycopg.Connection) -> None:
    _seed_chunks(conn)
    _act_as(conn, "rag_retriever", ["all-staff"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM rag.chunks")


def _seed_ops(conn: psycopg.Connection) -> None:
    conn.execute("RESET ROLE")
    conn.execute(
        """
        INSERT INTO ops.employees (employee_id, full_name, email, department, title, location,
                                   hire_date)
        VALUES ('EMP-T1', 'Test Person', 'test.person@example.test', 'Sales', 'AE', 'Denver',
                '2024-01-01')
        """
    )
    conn.execute(
        """
        INSERT INTO ops.employee_compensation (employee_id, base_salary_usd, bonus_pct, pay_band,
                                               effective_date)
        VALUES ('EMP-T1', 100000, 10, 'S3', '2026-01-01')
        """
    )


@pytest.mark.parametrize(
    ("groups", "visible"),
    [(["all-staff", "sales"], False), (["all-staff", "hr"], True), (["exec"], True)],
)
def test_compensation_view_filters_by_group(
    conn: psycopg.Connection, groups: list[str], visible: bool
) -> None:
    _seed_ops(conn)
    _act_as(conn, "rag_sql_readonly", groups)
    rows = conn.execute(
        "SELECT employee_id FROM analytics.employee_compensation WHERE employee_id = 'EMP-T1'"
    ).fetchall()
    assert bool(rows) is visible
    directory = conn.execute(
        "SELECT count(*) FROM analytics.employee_directory WHERE employee_id = 'EMP-T1'"
    ).fetchone()
    assert directory == (1,)


def test_sql_role_has_no_access_to_base_tables(conn: psycopg.Connection) -> None:
    _act_as(conn, "rag_sql_readonly", ["exec"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT * FROM ops.employee_compensation")
