"""The analytics views the SQL tool may query, described for the model.

This mirrors the views created by migrations 0004/0006 (an integration test keeps the two in
sync). `required_groups` only decides which views are *offered* to the model for a caller, which
avoids pointless queries; the database enforces the same rule independently.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass

SCHEMA = "analytics"


@dataclass(frozen=True)
class View:
    name: str
    description: str
    columns: tuple[tuple[str, str], ...]
    required_groups: frozenset[str] | None = None  # None: any authenticated caller

    def visible_to(self, groups: Collection[str]) -> bool:
        return bool(groups) and (
            self.required_groups is None or bool(self.required_groups & set(groups))
        )


VIEWS: tuple[View, ...] = (
    View(
        "customers",
        "One row per customer account. tier is gold, silver or bronze; sla_hours is the "
        "contractual delivery SLA; account_manager_id references employee_directory.",
        (
            ("customer_id", "text"),
            ("name", "text"),
            ("industry", "text"),
            ("tier", "text"),
            ("sla_hours", "integer"),
            ("region", "text"),
            ("account_manager_id", "text"),
            ("created_on", "date"),
        ),
    ),
    View(
        "shipments",
        "One row per shipment. mode is road, rail, air or sea; status is booked, in_transit, "
        "delivered or cancelled; is_late is true when delivered_at is after promised_at.",
        (
            ("shipment_id", "text"),
            ("customer_id", "text"),
            ("origin", "text"),
            ("destination", "text"),
            ("mode", "text"),
            ("status", "text"),
            ("weight_kg", "numeric"),
            ("booked_at", "timestamptz"),
            ("promised_at", "timestamptz"),
            ("delivered_at", "timestamptz"),
            ("is_late", "boolean"),
        ),
    ),
    View(
        "employee_directory",
        "One row per employee; manager_id references employee_id.",
        (
            ("employee_id", "text"),
            ("full_name", "text"),
            ("email", "text"),
            ("department", "text"),
            ("title", "text"),
            ("manager_id", "text"),
            ("location", "text"),
            ("hire_date", "date"),
        ),
    ),
    View(
        "invoices",
        "One invoice per shipment. status is open, paid, overdue or disputed.",
        (
            ("invoice_id", "text"),
            ("customer_id", "text"),
            ("shipment_id", "text"),
            ("amount_usd", "numeric"),
            ("issued_on", "date"),
            ("due_on", "date"),
            ("paid_on", "date"),
            ("status", "text"),
        ),
        required_groups=frozenset({"finance", "sales", "exec"}),
    ),
    View(
        "employee_compensation",
        "Current compensation per employee; join employee_directory on employee_id for names.",
        (
            ("employee_id", "text"),
            ("base_salary_usd", "numeric"),
            ("bonus_pct", "numeric"),
            ("pay_band", "text"),
            ("effective_date", "date"),
        ),
        required_groups=frozenset({"hr", "exec"}),
    ),
)


def views_for(groups: Collection[str]) -> list[View]:
    return [view for view in VIEWS if view.visible_to(groups)]


def describe(views: Iterable[View]) -> str:
    """Compact DDL-like description; models write better SQL from schema-shaped text."""
    blocks = []
    for view in views:
        columns = ",\n".join(f"  {name} {kind}" for name, kind in view.columns)
        blocks.append(f"-- {view.description}\n{SCHEMA}.{view.name} (\n{columns}\n)")
    return "\n\n".join(blocks)
