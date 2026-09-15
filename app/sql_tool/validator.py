"""Static guard for model-written SQL: parse, allowlist, normalise, cap.

This is the first of two independent layers; the second is the database (migration 0006: a
dedicated login that owns nothing and can switch to no other role, a read-only transaction, and
views that read the caller's groups from a context the query cannot change). It is strict on
purpose: anything it does not recognise is rejected, and what runs is SQL regenerated from the
parsed tree, never the model's text, so comments and quoting tricks cannot survive.

Functions sqlglot does not model come out as `Anonymous` nodes. That is exactly where the
dangerous Postgres functions live (`set_config`, `current_setting`, `pg_sleep`, `dblink`,
`pg_read_file`, `lo_import`, ...), so unmodelled functions are rejected unless allowlisted.
"""

from collections.abc import Collection
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.sql_tool.catalog import SCHEMA


class SQLRejected(ValueError):
    """The query is not safe to run; `reason` is short and safe to show the model on retry."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


SAFE_UNMODELLED_FUNCTIONS = frozenset({"age", "make_date", "make_interval", "date_bin"})

FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Command,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
    exp.Lock,
    exp.Into,
)


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    views: tuple[str, ...]
    max_rows: int


def _literal_limit(query: exp.Query) -> int | None:
    limit = query.args.get("limit")
    if isinstance(limit, exp.Limit) and isinstance(limit.expression, exp.Literal):
        value = limit.expression
        if not value.is_string and value.this.isdigit():
            return int(value.this)
    return None


def validate_sql(sql: str, allowed_views: Collection[str], *, max_rows: int) -> ValidatedSQL:
    text = sql.strip().removesuffix(";").strip()
    if not text:
        raise SQLRejected("the query is empty")
    try:
        statements = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
    except SqlglotError as exc:
        raise SQLRejected("the query could not be parsed as PostgreSQL") from exc
    if len(statements) != 1:
        raise SQLRejected("exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise SQLRejected("only SELECT queries are allowed")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLRejected(f"{type(node).__name__.upper()} is not allowed")
        if isinstance(node, exp.Anonymous | exp.AnonymousAggFunc):
            name = str(node.this).lower()
            if name not in SAFE_UNMODELLED_FUNCTIONS:
                raise SQLRejected(f"function {name}() is not allowed")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    allowed = {view.lower() for view in allowed_views}
    used: set[str] = set()
    for table in list(tree.find_all(exp.Table)):
        name, schema = table.name.lower(), table.db.lower()
        if not schema and not table.catalog and name in cte_names:
            continue
        if not name or table.catalog or schema not in ("", SCHEMA) or name not in allowed:
            raise SQLRejected(f"{table.sql(dialect='postgres')} is not an available view")
        table.set("db", exp.to_identifier(SCHEMA))
        used.add(name)
    if not used:
        raise SQLRejected(f"the query must read from at least one {SCHEMA} view")

    limit = _literal_limit(tree)
    if limit is None or limit > max_rows:
        if isinstance(tree, exp.Select):
            tree = tree.limit(max_rows, copy=False)
        else:  # UNION / INTERSECT / EXCEPT: cap the combined result.
            tree = exp.select("*").from_(tree.subquery("capped")).limit(max_rows)
    return ValidatedSQL(tree.sql(dialect="postgres"), tuple(sorted(used)), max_rows)
