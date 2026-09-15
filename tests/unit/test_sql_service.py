from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from app.auth import Principal
from app.llm.base import Completion, Usage
from app.sql_tool.executor import QueryResult, SQLExecutionError
from app.sql_tool.service import SqlOutcome, SqlTool, extract_sql, result_source
from app.sql_tool.validator import ValidatedSQL

SUPPORT = Principal("sam", frozenset({"all-staff", "support"}))
SALES = Principal("ana", frozenset({"all-staff", "sales"}))
AS_OF = date(2026, 8, 31)


class RepliesLLM:
    name = "scripted"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.systems: list[str] = []

    @property
    def model(self) -> str:
        return "claude-haiku-4-5"

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> Completion:
        self.systems.append(system)
        self.prompts.append(prompt)
        return Completion(self.replies.pop(0), self.model, self.name, Usage(1000, 100), None, 9.0)


class StubExecutor:
    def __init__(self, *outcomes: QueryResult | SQLExecutionError) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[ValidatedSQL, list[str]]] = []

    def run(self, query: ValidatedSQL, groups: Sequence[str]) -> QueryResult:
        self.calls.append((query, list(groups)))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, SQLExecutionError):
            raise outcome
        return outcome


def _result(sql: str = "SELECT 1") -> QueryResult:
    return QueryResult(sql, ("late",), [(42,)], truncated=False)


def _tool(llm: RepliesLLM, executor: StubExecutor) -> SqlTool:
    return SqlTool(llm, executor, max_rows=100, as_of=AS_OF)


def test_extract_sql_prefers_the_fenced_block() -> None:
    assert extract_sql("Here:\n```sql\nSELECT 1\n```\nDone") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"


def test_successful_query_is_validated_run_for_the_callers_groups_and_billed() -> None:
    llm = RepliesLLM("```sql\nSELECT count(*) AS late FROM shipments WHERE is_late\n```")
    executor = StubExecutor(_result())

    outcome = _tool(llm, executor).run("How many shipments were late?", SUPPORT)

    query, groups = executor.calls[0]
    assert query.sql == "SELECT COUNT(*) AS late FROM analytics.shipments WHERE is_late LIMIT 100"
    assert groups == ["all-staff", "support"]
    assert outcome.result is not None
    assert outcome.views == ("shipments",)
    assert outcome.attempts == 1
    assert outcome.cost_usd == Decimal("0.0015")


def test_prompt_offers_only_visible_views_and_the_reference_date() -> None:
    llm = RepliesLLM("CANNOT_ANSWER")

    _tool(llm, StubExecutor()).run("Anything", SUPPORT)

    assert "analytics.shipments" in llm.prompts[0]
    assert "invoices" not in llm.prompts[0]
    assert "employee_compensation" not in llm.prompts[0]
    assert "Today is 2026-08-31" in llm.systems[0]


def test_rejected_query_is_retried_with_the_reason() -> None:
    llm = RepliesLLM(
        "SELECT set_config('role', 'rag', true) FROM customers",
        "SELECT count(*) FROM customers",
    )
    executor = StubExecutor(_result())

    outcome = _tool(llm, executor).run("How many customers?", SUPPORT)

    assert outcome.attempts == 2
    assert outcome.error is None
    assert "set_config() is not allowed" in llm.prompts[1]
    assert outcome.usage == Usage(2000, 200)
    assert len(executor.calls) == 1


def test_restricted_views_are_never_executed() -> None:
    attempt = "SELECT avg(base_salary_usd) FROM analytics.employee_compensation"
    llm = RepliesLLM(attempt, attempt)
    executor = StubExecutor()

    outcome = _tool(llm, executor).run("Average salary?", SALES)

    assert outcome.result is None
    assert outcome.error is not None
    assert "not an available view" in outcome.error
    assert executor.calls == []


def test_database_errors_are_fed_back_once() -> None:
    llm = RepliesLLM("SELECT nme FROM customers", "SELECT name FROM customers")
    executor = StubExecutor(SQLExecutionError('column "nme" does not exist'), _result())

    outcome = _tool(llm, executor).run("Customer names", SUPPORT)

    assert outcome.result is not None
    assert 'column "nme" does not exist' in llm.prompts[1]


def test_timeouts_are_not_retried() -> None:
    llm = RepliesLLM("SELECT * FROM shipments", "SELECT * FROM shipments")
    executor = StubExecutor(SQLExecutionError("the query timed out", timeout=True))

    outcome = _tool(llm, executor).run("Everything", SUPPORT)

    assert outcome.attempts == 1
    assert outcome.error == "the query timed out"


def test_model_refusal_and_callers_without_views() -> None:
    refused = _tool(RepliesLLM("CANNOT_ANSWER"), StubExecutor()).run("Weather?", SUPPORT)
    nobody = _tool(RepliesLLM(), StubExecutor()).run("Anything", Principal("x", frozenset()))

    assert refused.error == "cannot_answer"
    assert nobody.error == "no_accessible_views"
    assert nobody.attempts == 0


def test_result_source_renders_a_citable_table() -> None:
    result = QueryResult(
        "SELECT name, amount FROM analytics.invoices LIMIT 2",
        ("name", "amount", "paid_on"),
        [("Acme", Decimal("12.50"), None), ("Brightline", Decimal("7"), date(2026, 8, 1))],
        truncated=True,
    )
    outcome = SqlOutcome(result, result.sql, None, views=("invoices",))

    source = result_source(outcome, ["sales", "all-staff"])

    assert source.source_type == "sql"
    assert source.source_uri == "sql://analytics/invoices"
    assert "2 row(s), capped: more rows exist" in source.content
    assert "Acme | 12.50 | NULL" in source.content
    assert "Brightline | 7 | 2026-08-01" in source.content
    assert source.allowed_groups == ("all-staff", "sales")
