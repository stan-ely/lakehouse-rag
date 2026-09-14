"""Operational (system-of-record) schema for Larkspur Logistics.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA ops")
    op.execute(
        """
        CREATE TABLE ops.employees (
            employee_id  text PRIMARY KEY,
            full_name    text NOT NULL,
            email        text NOT NULL UNIQUE,
            department   text NOT NULL,
            title        text NOT NULL,
            manager_id   text REFERENCES ops.employees (employee_id),
            location     text NOT NULL,
            hire_date    date NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops.employee_compensation (
            employee_id     text PRIMARY KEY REFERENCES ops.employees (employee_id),
            base_salary_usd numeric(12, 2) NOT NULL CHECK (base_salary_usd > 0),
            bonus_pct       numeric(5, 2) NOT NULL CHECK (bonus_pct >= 0),
            pay_band        text NOT NULL,
            effective_date  date NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops.customers (
            customer_id        text PRIMARY KEY,
            name               text NOT NULL UNIQUE,
            industry           text NOT NULL,
            tier               text NOT NULL CHECK (tier IN ('gold', 'silver', 'bronze')),
            sla_hours          integer NOT NULL CHECK (sla_hours > 0),
            region             text NOT NULL,
            account_manager_id text NOT NULL REFERENCES ops.employees (employee_id),
            created_on         date NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops.shipments (
            shipment_id  text PRIMARY KEY,
            customer_id  text NOT NULL REFERENCES ops.customers (customer_id),
            origin       text NOT NULL,
            destination  text NOT NULL,
            mode         text NOT NULL CHECK (mode IN ('road', 'rail', 'air', 'sea')),
            status       text NOT NULL
                         CHECK (status IN ('booked', 'in_transit', 'delivered', 'cancelled')),
            weight_kg    numeric(10, 2) NOT NULL CHECK (weight_kg > 0),
            booked_at    timestamptz NOT NULL,
            promised_at  timestamptz NOT NULL,
            delivered_at timestamptz,
            CHECK (promised_at > booked_at),
            CHECK (delivered_at IS NULL OR status = 'delivered')
        )
        """
    )
    op.execute("CREATE INDEX shipments_customer_idx ON ops.shipments (customer_id, booked_at)")
    op.execute(
        """
        CREATE TABLE ops.invoices (
            invoice_id  text PRIMARY KEY,
            customer_id text NOT NULL REFERENCES ops.customers (customer_id),
            shipment_id text NOT NULL UNIQUE REFERENCES ops.shipments (shipment_id),
            amount_usd  numeric(12, 2) NOT NULL CHECK (amount_usd > 0),
            issued_on   date NOT NULL,
            due_on      date NOT NULL,
            paid_on     date,
            status      text NOT NULL CHECK (status IN ('open', 'paid', 'overdue', 'disputed')),
            CHECK (due_on >= issued_on)
        )
        """
    )
    op.execute("CREATE INDEX invoices_customer_issued_idx ON ops.invoices (customer_id, issued_on)")


def downgrade() -> None:
    op.execute("DROP SCHEMA ops CASCADE")
