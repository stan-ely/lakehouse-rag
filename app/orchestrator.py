"""One query end to end: route it, run the tools the route needs, generate a grounded answer.

Degradation is explicit and never widens access:
- `sql` whose query cannot be produced or run falls back to document retrieval (or a refusal);
- `hybrid` whose SQL model call fails answers from documents alone;
- `sql` whose SQL model call fails surfaces the provider error, like any generation failure.
"""

import time
from dataclasses import replace
from decimal import Decimal
from typing import Protocol

from app.auth import Principal
from app.generation.answer import Answer, AnswerService
from app.llm.base import LLMError, Usage
from app.llm.cost import add_costs
from app.observability.tracing import traced
from app.router.classifier import Route, RouteDecision, document_subquery
from app.sql_tool.service import SqlOutcome, result_source


class Routes(Protocol):
    def route(self, question: str) -> RouteDecision: ...


class SqlRunner(Protocol):
    def run(self, question: str, principal: Principal) -> SqlOutcome: ...


class QueryService:
    def __init__(self, router: Routes, answers: AnswerService, sql_tool: SqlRunner | None) -> None:
        self.router = router
        self.answers = answers
        self.sql_tool = sql_tool

    @traced("CHAIN")
    def answer(self, question: str, principal: Principal) -> Answer:
        start = time.perf_counter()
        decision = self.router.route(question)
        timings = {"routing": (time.perf_counter() - start) * 1000}

        outcome: SqlOutcome | None = None
        if decision.route in (Route.SQL, Route.HYBRID) and self.sql_tool is not None:
            try:
                outcome = self.sql_tool.run(question, principal)
                timings["sql"] = outcome.latency_ms
            except LLMError:
                if decision.route is Route.SQL:
                    raise

        sources = []
        if outcome is not None and outcome.result is not None:
            sources.append(result_source(outcome, sorted(principal.groups)))
        retrieve = decision.route is not Route.SQL or not sources
        # Only hybrid: its question carries a records clause naming an entity, which buries the
        # policy page the document leg is looking for. A docs question has no such clause.
        subquery = document_subquery(question) if decision.route is Route.HYBRID else None
        answer = self.answers.answer(
            question,
            principal,
            extra_sources=sources,
            retrieve=retrieve,
            retrieval_query=subquery,
        )

        sql_usage = outcome.usage if outcome else Usage()
        sql_cost = outcome.cost_usd if outcome else Decimal(0)
        return replace(
            answer,
            route=decision.route.value,
            route_method=decision.method,
            sql=outcome.sql if outcome else None,
            sql_error=outcome.error if outcome else None,
            usage=decision.usage + sql_usage + answer.usage,
            cost_usd=add_costs(decision.cost_usd, sql_cost, answer.cost_usd),
            timings_ms={**timings, **answer.timings_ms},
        )
