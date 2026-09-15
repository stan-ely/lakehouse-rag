"""Runs validated SQL as the least-privileged login, inside a read-only transaction.

The statement order is the security boundary (see migration 0006):
1. `authz.begin_sql_request()` records the caller's groups for this transaction only;
2. the transaction becomes read-only, so the query can neither rewrite that context nor switch
   back to read-write;
3. the statement timeout is set, then the query runs and everything is rolled back.
The pool must log in as `rag_sql` with autocommit on, so `transaction()` issues BEGIN.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import Connection, errors
from psycopg_pool import ConnectionPool

from app.sql_tool.validator import ValidatedSQL


class SQLExecutionError(Exception):
    """The database rejected or cancelled the query; the message is safe to show the model."""

    def __init__(self, message: str, *, timeout: bool = False) -> None:
        super().__init__(message)
        self.timeout = timeout


@dataclass(frozen=True)
class QueryResult:
    sql: str
    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]]
    truncated: bool


class SqlExecutor:
    def __init__(
        self, pool: ConnectionPool[Connection[Any]], *, statement_timeout_ms: int = 5000
    ) -> None:
        self.pool = pool
        self.statement_timeout_ms = statement_timeout_ms

    def run(self, query: ValidatedSQL, groups: Sequence[str]) -> QueryResult:
        caller_groups = sorted({g for g in groups if g})
        try:
            with (
                self.pool.connection() as conn,
                conn.transaction(force_rollback=True),
                conn.cursor() as cur,
            ):
                cur.execute("SELECT authz.begin_sql_request(%s::text[])", (caller_groups,))
                cur.execute("SET LOCAL transaction_read_only = on")
                cur.execute(f"SET LOCAL statement_timeout = {int(self.statement_timeout_ms)}")
                cur.execute(query.sql)
                columns = tuple(column.name for column in cur.description or ())
                rows = cur.fetchmany(query.max_rows + 1)
        except errors.QueryCanceled as exc:
            raise SQLExecutionError("the query timed out", timeout=True) from exc
        except psycopg.Error as exc:
            raise SQLExecutionError(str(exc).strip().splitlines()[0]) from exc
        return QueryResult(query.sql, columns, rows[: query.max_rows], len(rows) > query.max_rows)
