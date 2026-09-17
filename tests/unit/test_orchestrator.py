from collections.abc import Sequence
from decimal import Decimal

import pytest

from app.auth import Principal
from app.generation.answer import AnswerService
from app.llm.base import Completion, LLMError, Usage
from app.orchestrator import QueryService
from app.retrieval.hybrid import RetrievedChunk
from app.router.classifier import Route, RouteDecision
from app.sql_tool.executor import QueryResult
from app.sql_tool.service import SqlOutcome
from tests.unit.factories import make_chunk

PRINCIPAL = Principal("ana", frozenset({"all-staff", "sales"}))
RESULT = QueryResult("SELECT 3 AS late", ("late",), [(3,)], truncated=False)


class FixedRouter:
    def __init__(self, route: Route, cost: Decimal | None = Decimal("0.0001")) -> None:
        self.decision = RouteDecision(route, "llm", "claude-haiku-4-5", Usage(100, 1), cost)

    def route(self, question: str) -> RouteDecision:
        return self.decision


class StubSql:
    def __init__(self, outcome: SqlOutcome | None = None, error: LLMError | None = None) -> None:
        self.outcome = outcome
        self.error = error
        self.calls = 0

    def run(self, question: str, principal: Principal) -> SqlOutcome:
        self.calls += 1
        if self.error:
            raise self.error
        assert self.outcome is not None
        return self.outcome


class StubRetriever:
    def __init__(self) -> None:
        self.calls = 0
        self.queries: list[str] = []

    def search(self, query: str, groups: Sequence[str], k: int) -> list[RetrievedChunk]:
        self.calls += 1
        self.queries.append(query)
        return [make_chunk(1)]


class EchoPromptLLM:
    """Cites [1] and records which sources were offered."""

    name = "scripted"
    model = "claude-haiku-4-5"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> Completion:
        self.prompts.append(prompt)
        return Completion("The answer is here [1].", self.model, self.name, Usage(1000, 10), "", 1)


def _service(
    route: Route, sql: StubSql | None, router_cost: Decimal | None = Decimal("0.0001")
) -> tuple[QueryService, StubRetriever, EchoPromptLLM]:
    retriever, llm = StubRetriever(), EchoPromptLLM()
    answers = AnswerService(retriever, llm, k=3)
    return QueryService(FixedRouter(route, router_cost), answers, sql), retriever, llm


def _sql_outcome(result: QueryResult | None = RESULT, error: str | None = None) -> SqlOutcome:
    cost = Decimal("0.002")
    return SqlOutcome(result, "SELECT 3", error, ("shipments",), 1, Usage(500, 50), cost, 20.0)


def test_docs_route_never_runs_sql() -> None:
    sql = StubSql(_sql_outcome())
    service, retriever, _ = _service(Route.DOCS, sql)

    answer = service.answer("What is the travel policy?", PRINCIPAL)

    assert sql.calls == 0
    assert retriever.calls == 1
    assert answer.route == "docs"
    assert answer.sql is None


def test_sql_route_answers_from_the_query_result_and_sums_costs() -> None:
    service, retriever, llm = _service(Route.SQL, StubSql(_sql_outcome()))

    answer = service.answer("How many late shipments?", PRINCIPAL)

    assert retriever.calls == 0
    assert 'type="sql"' in llm.prompts[0]
    assert answer.citations[0].source_uri == "sql://analytics/shipments"
    assert answer.sql == "SELECT 3"
    assert answer.usage == Usage(1600, 61)
    # router 0.0001 + sql 0.002 + answer (1000 x $1 + 10 x $5) / 1M
    assert answer.cost_usd == Decimal("0.00315")
    assert set(answer.timings_ms) >= {"routing", "sql", "generation"}


def test_sql_route_falls_back_to_documents_when_the_query_fails() -> None:
    service, retriever, _ = _service(Route.SQL, StubSql(_sql_outcome(None, "not allowed")))

    answer = service.answer("How many late shipments?", PRINCIPAL)

    assert retriever.calls == 1
    assert answer.sql_error == "not allowed"
    assert answer.citations[0].source_uri.startswith("s3://")


def test_hybrid_route_offers_the_sql_result_first_then_documents() -> None:
    service, retriever, llm = _service(Route.HYBRID, StubSql(_sql_outcome()))

    service.answer("What is the SLA and how many late shipments?", PRINCIPAL)

    assert retriever.calls == 1
    assert llm.prompts[0].index('type="sql"') < llm.prompts[0].index('type="wiki"')


def test_hybrid_degrades_to_documents_when_the_sql_model_fails() -> None:
    service, retriever, _ = _service(Route.HYBRID, StubSql(error=LLMError("down")))

    answer = service.answer("SLA and late shipments?", PRINCIPAL)

    assert retriever.calls == 1
    assert not answer.refused


def test_sql_route_surfaces_model_failures() -> None:
    service, _, _ = _service(Route.SQL, StubSql(error=LLMError("down")))

    with pytest.raises(LLMError):
        service.answer("How many late shipments?", PRINCIPAL)


def test_unknown_component_cost_makes_the_total_unknown() -> None:
    service, _, _ = _service(Route.DOCS, None, router_cost=None)

    assert service.answer("Policy?", PRINCIPAL).cost_usd is None


def test_hybrid_retrieval_searches_the_document_clauses_only() -> None:
    # The records clause names a customer, and that name pulls every ticket mentioning them
    # above the policy page. The SQL leg and the generation prompt still see the whole question.
    service, retriever, llm = _service(Route.HYBRID, StubSql(_sql_outcome()))
    question = (
        "What first response time does our SLA policy promise Hardy Outfitters, "
        "and how many of their shipments were delivered late in August 2026?"
    )

    service.answer(question, PRINCIPAL)

    assert retriever.queries == [
        "What first response time does our SLA policy promise Hardy Outfitters."
    ]
    assert question in llm.prompts[0]


def test_docs_route_searches_the_whole_question() -> None:
    service, retriever, _ = _service(Route.DOCS, None)

    service.answer("What is the travel policy, and how do I claim a per diem?", PRINCIPAL)

    assert retriever.queries == ["What is the travel policy, and how do I claim a per diem?"]
