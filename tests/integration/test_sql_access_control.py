"""Database isolation for the SQL tool, tested with the validator deliberately bypassed.

Every query here is handed straight to the executor, as if prompt injection had produced SQL the
validator failed to catch. The database alone must still keep callers inside their groups.
"""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg import Connection
from psycopg_pool import ConnectionPool

from app.settings import Settings
from app.sql_tool.catalog import VIEWS
from app.sql_tool.executor import SQLExecutionError, SqlExecutor
from app.sql_tool.validator import ValidatedSQL

pytestmark = pytest.mark.integration

SETTINGS = Settings()
HR = ["all-staff", "hr"]
SALES = ["all-staff", "sales"]
SUPPORT = ["all-staff", "support"]


@pytest.fixture(scope="module")
def executor() -> Iterator[SqlExecutor]:
    with ConnectionPool[Connection[Any]](
        SETTINGS.sql_dsn, min_size=1, max_size=2, kwargs={"autocommit": True}
    ) as pool:
        yield SqlExecutor(pool, statement_timeout_ms=1000)


def _unvalidated(sql: str, max_rows: int = 500) -> ValidatedSQL:
    return ValidatedSQL(sql, (), max_rows)


def _count(executor: SqlExecutor, sql: str, groups: list[str]) -> int:
    value = executor.run(_unvalidated(sql), groups).rows[0][0]
    assert isinstance(value, int)
    return value


def test_sensitive_views_follow_the_callers_groups(executor: SqlExecutor) -> None:
    compensation = "SELECT count(*) FROM analytics.employee_compensation"
    invoices = "SELECT count(*) FROM analytics.invoices"

    assert _count(executor, compensation, HR) > 0
    assert _count(executor, compensation, SALES) == 0
    assert _count(executor, invoices, SALES) > 0
    assert _count(executor, invoices, SUPPORT) == 0
    assert _count(executor, "SELECT count(*) FROM analytics.shipments", SUPPORT) > 0


def test_widening_groups_with_set_config_reveals_nothing(executor: SqlExecutor) -> None:
    sql = (
        "WITH g AS MATERIALIZED (SELECT set_config('app.user_groups', 'hr,exec', true) AS s) "
        "SELECT count(*) FROM g, analytics.employee_compensation"
    )
    assert _count(executor, sql, SALES) == 0


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        pytest.param("SELECT set_config('role', 'rag', true)", "permission denied", id="role"),
        pytest.param(
            "SELECT set_config('transaction_read_only', 'off', true)", "read-write", id="rw"
        ),
        pytest.param(
            "SELECT authz.begin_sql_request(ARRAY['hr'])", "read-only transaction", id="context"
        ),
        pytest.param(
            "SELECT count(*) FROM ops.employee_compensation", "permission denied", id="base-table"
        ),
        pytest.param(
            "WITH d AS (DELETE FROM analytics.customers RETURNING 1) SELECT count(*) FROM d",
            "read-only|permission denied",
            id="delete",
        ),
    ],
)
def test_escalation_attempts_fail_inside_the_database(
    executor: SqlExecutor, sql: str, error: str
) -> None:
    with pytest.raises(SQLExecutionError, match=error):
        executor.run(_unvalidated(sql), SALES)


def test_statement_timeout_cancels_slow_queries(executor: SqlExecutor) -> None:
    with pytest.raises(SQLExecutionError, match="timed out") as info:
        executor.run(_unvalidated("SELECT pg_sleep(5)"), SALES)
    assert info.value.timeout


def test_results_are_capped_and_flagged(executor: SqlExecutor) -> None:
    result = executor.run(
        _unvalidated("SELECT shipment_id FROM analytics.shipments", max_rows=10), SUPPORT
    )
    assert result.columns == ("shipment_id",)
    assert len(result.rows) == 10
    assert result.truncated


def test_request_context_never_persists(executor: SqlExecutor) -> None:
    executor.run(_unvalidated("SELECT 1"), HR)
    with psycopg.connect(SETTINGS.dsn) as conn:
        assert conn.execute("SELECT count(*) FROM authz.sql_request_context").fetchone() == (0,)


def test_catalog_matches_the_database() -> None:
    aliases = {"timestamp with time zone": "timestamptz"}
    with psycopg.connect(SETTINGS.dsn) as conn:
        rows = conn.execute(
            """
            SELECT table_name, column_name, data_type FROM information_schema.columns
            WHERE table_schema = 'analytics' ORDER BY table_name, ordinal_position
            """
        ).fetchall()
    database: dict[str, list[tuple[str, str]]] = {}
    for table, column, kind in rows:
        database.setdefault(table, []).append((column, aliases.get(kind, str(kind))))

    assert database == {view.name: list(view.columns) for view in VIEWS}
