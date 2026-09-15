"""SQL tool isolation that holds even if the SQL validator is bypassed.

Migration 0004 filtered the sensitive views on the `app.user_groups` setting. A probe showed that
a single SELECT running as `rag_sql_readonly` defeats that: `set_config('app.user_groups', 'hr',
true)` inside a CTE widens its own groups, and `set_config('role', 'rag', true)` switches back to
the session role. The validator rejects both, but the database layer must hold on its own:

- The SQL tool logs in as `rag_sql`, a member of `rag_sql_readonly` and nothing else. A role
  switch can only reach roles the session user belongs to, so there is nothing to escalate to.
- The caller's groups are written to `authz.sql_request_context` by the SECURITY DEFINER function
  `authz.begin_sql_request()`, keyed by the current transaction id. The tool then makes the
  transaction read-only; from then on Postgres refuses writes (the context cannot be rewritten)
  and refuses any switch back to read-write.
- The sensitive views read `authz.sql_request_groups()`, which returns no groups unless the
  transaction is read-only and owns a context row, so every other path fails closed.
- The tool always rolls back, so context rows do not persist. Rows left by a caller that
  commits are inert: they are keyed by a transaction id that is never reused.

Retrieval (`rag.chunks` RLS) still uses `app.user_groups`: it never runs caller-written SQL.
Login credentials are not created here; see `db/local_logins.py` for local and CI.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "rag_sql_readonly"
LOGIN = "rag_sql"

SENSITIVE_VIEWS: dict[str, tuple[str, str]] = {
    "invoices": (
        """
        SELECT invoice_id, customer_id, shipment_id, amount_usd, issued_on, due_on, paid_on, status
        FROM ops.invoices
        """,
        "ARRAY['finance', 'sales', 'exec']",
    ),
    "employee_compensation": (
        """
        SELECT employee_id, base_salary_usd, bonus_pct, pay_band, effective_date
        FROM ops.employee_compensation
        """,
        "ARRAY['hr', 'exec']",
    ),
}


def _replace_views(groups_expression: str) -> None:
    for name, (body, required) in SENSITIVE_VIEWS.items():
        op.execute(
            f"""
            CREATE OR REPLACE VIEW analytics.{name} WITH (security_barrier = true) AS
            {body} WHERE {groups_expression} && {required}
            """
        )


def upgrade() -> None:
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{LOGIN}') THEN
                CREATE ROLE {LOGIN} NOLOGIN;
            END IF;
        END $$
        """
    )
    op.execute(f"GRANT {ROLE} TO {LOGIN}")
    op.execute(f"ALTER ROLE {LOGIN} SET statement_timeout = '5s'")

    op.execute(
        """
        CREATE UNLOGGED TABLE authz.sql_request_context (
            xid         xid8 PRIMARY KEY,
            backend_pid integer NOT NULL,
            groups      text[] NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION authz.begin_sql_request(caller_groups text[]) RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
            INSERT INTO authz.sql_request_context (xid, backend_pid, groups)
            VALUES (pg_current_xact_id(), pg_backend_pid(), coalesce(caller_groups, '{}'))
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION authz.sql_request_groups() RETURNS text[]
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
            SELECT coalesce(
                (SELECT groups FROM authz.sql_request_context
                 WHERE xid = pg_current_xact_id_if_assigned()
                   AND backend_pid = pg_backend_pid()
                   AND current_setting('transaction_read_only') = 'on'),
                '{}'::text[]
            )
        $$
        """
    )
    for function in ("begin_sql_request(text[])", "sql_request_groups()"):
        op.execute(f"REVOKE ALL ON FUNCTION authz.{function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION authz.{function} TO {ROLE}")

    # Wrapped in a scalar subquery so the function runs once per query, not once per row.
    _replace_views("(SELECT authz.sql_request_groups())")
    op.execute(f"REVOKE EXECUTE ON FUNCTION authz.current_groups() FROM {ROLE}")


def downgrade() -> None:
    op.execute(f"GRANT EXECUTE ON FUNCTION authz.current_groups() TO {ROLE}")
    _replace_views("authz.current_groups()")
    op.execute("DROP FUNCTION authz.sql_request_groups()")
    op.execute("DROP FUNCTION authz.begin_sql_request(text[])")
    op.execute("DROP TABLE authz.sql_request_context")
    op.execute(f"ALTER ROLE {LOGIN} RESET statement_timeout")
    op.execute(f"REVOKE {ROLE} FROM {LOGIN}")
    op.execute(f"DROP ROLE IF EXISTS {LOGIN}")
