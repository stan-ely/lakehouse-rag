"""Guarded analytics views for the text-to-SQL tool.

`rag_sql_readonly` can only see the `analytics` schema; it has no privileges on `ops`.
The views run with their owner's privileges and are `security_barrier`, so sensitive
columns and rows are filtered inside the view using the caller's `app.user_groups`:
- employee compensation: hr, exec
- invoices: finance, sales, exec
The SQL tool's sqlglot validator enforces the same allowlist before a query ever reaches
Postgres; this layer holds even if the validator is bypassed.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "rag_sql_readonly"

VIEWS: dict[str, str] = {
    "customers": """
        SELECT customer_id, name, industry, tier, sla_hours, region, account_manager_id, created_on
        FROM ops.customers
    """,
    "employee_directory": """
        SELECT employee_id, full_name, email, department, title, manager_id, location, hire_date
        FROM ops.employees
    """,
    "shipments": """
        SELECT shipment_id, customer_id, origin, destination, mode, status, weight_kg,
               booked_at, promised_at, delivered_at,
               (delivered_at IS NOT NULL AND delivered_at > promised_at) AS is_late
        FROM ops.shipments
    """,
    "invoices": """
        SELECT invoice_id, customer_id, shipment_id, amount_usd, issued_on, due_on, paid_on, status
        FROM ops.invoices
        WHERE authz.current_groups() && ARRAY['finance', 'sales', 'exec']
    """,
    "employee_compensation": """
        SELECT employee_id, base_salary_usd, bonus_pct, pay_band, effective_date
        FROM ops.employee_compensation
        WHERE authz.current_groups() && ARRAY['hr', 'exec']
    """,
}


def upgrade() -> None:
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} NOLOGIN;
            END IF;
        END $$
        """
    )
    # Applies when a login role inherits these settings at connect; the SQL tool also sets
    # them per transaction because SET ROLE does not apply role-level GUCs.
    op.execute(f"ALTER ROLE {ROLE} SET default_transaction_read_only = on")
    op.execute(f"ALTER ROLE {ROLE} SET statement_timeout = '5s'")

    op.execute("CREATE SCHEMA analytics")
    for name, body in VIEWS.items():
        op.execute(f"CREATE VIEW analytics.{name} WITH (security_barrier = true) AS {body}")
        op.execute(f"GRANT SELECT ON analytics.{name} TO {ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA analytics, authz TO {ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION authz.current_groups() TO {ROLE}")


def downgrade() -> None:
    op.execute("DROP SCHEMA analytics CASCADE")
    op.execute(f"DROP OWNED BY {ROLE}")
    op.execute(f"DROP ROLE IF EXISTS {ROLE}")
