"""Text-to-SQL against the seeded ops data, with a scripted model and the real executor."""

from collections.abc import Iterator
from typing import Any

import pytest
from psycopg import Connection
from psycopg_pool import ConnectionPool

from app.auth import Principal
from app.llm.base import Completion, Usage
from app.settings import Settings
from app.sql_tool.executor import SqlExecutor
from app.sql_tool.service import SqlTool

pytestmark = pytest.mark.integration

SETTINGS = Settings()


class ScriptedSQL:
    name = "scripted"
    model = "claude-haiku-4-5"

    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.calls = 0

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> Completion:
        self.calls += 1
        return Completion(f"```sql\n{self.sql}\n```", self.model, self.name, Usage(800, 60), "", 1)


@pytest.fixture(scope="module")
def executor() -> Iterator[SqlExecutor]:
    with ConnectionPool[Connection[Any]](
        SETTINGS.sql_dsn, min_size=1, max_size=2, kwargs={"autocommit": True}
    ) as pool:
        yield SqlExecutor(pool)


def _run(executor: SqlExecutor, sql: str, groups: set[str]) -> Any:
    tool = SqlTool(ScriptedSQL(sql), executor, max_rows=50, as_of=SETTINGS.as_of_date)
    return tool.run("question", Principal("it-user", frozenset(groups)))


def test_late_shipments_per_customer_for_support(executor: SqlExecutor) -> None:
    outcome = _run(
        executor,
        """
        SELECT c.name, count(*) AS late_shipments
        FROM shipments s JOIN customers c USING (customer_id)
        WHERE s.is_late
        GROUP BY c.name ORDER BY late_shipments DESC, c.name
        """,
        {"all-staff", "support"},
    )

    assert outcome.error is None, outcome.error
    assert outcome.result.columns == ("name", "late_shipments")
    assert outcome.result.rows
    assert outcome.views == ("customers", "shipments")


def test_hr_can_aggregate_compensation(executor: SqlExecutor) -> None:
    outcome = _run(
        executor,
        "SELECT pay_band, round(avg(base_salary_usd)) FROM employee_compensation GROUP BY 1",
        {"all-staff", "hr"},
    )

    assert outcome.error is None, outcome.error
    assert outcome.result.rows


def test_sales_cannot_be_talked_into_compensation(executor: SqlExecutor) -> None:
    outcome = _run(
        executor,
        "SELECT avg(base_salary_usd) FROM analytics.employee_compensation",
        {"all-staff", "sales"},
    )

    assert outcome.result is None
    assert "not an available view" in outcome.error
