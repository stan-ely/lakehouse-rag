"""Prometheus metrics for the query path.

Label values are all closed sets — route, outcome, model — so cardinality is bounded. Nothing
derived from a question, a document or a caller is ever a label: a per-user counter would turn
the metrics endpoint into an unauthenticated record of who asked how much.

Cost is a counter in USD. It is a float, so it drifts in the last decimal place over millions
of requests; that is acceptable for a spend dashboard and is not the billing record.
"""

from decimal import Decimal

from prometheus_client import Counter, Histogram

from app.generation.answer import Answer

QUERIES = Counter("rag_queries_total", "Queries served", ["route", "outcome"])
LATENCY = Histogram(
    "rag_query_seconds",
    "End-to-end query latency",
    ["route"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 30),
)
TOKENS = Counter("rag_llm_tokens_total", "Tokens billed", ["model", "kind"])
COST = Counter("rag_llm_cost_usd_total", "Model spend in USD", ["model"])
FLAGGED_SOURCES = Counter(
    "rag_flagged_sources_total", "Retrieved chunks containing a prompt-injection attempt"
)
RATE_LIMITED = Counter("rag_rate_limited_total", "Queries rejected by the rate limiter")
UNKNOWN_COST = Counter(
    "rag_unknown_cost_total", "Completions from a model with no price in app.llm.cost"
)


def _outcome(answer: Answer) -> str:
    if answer.refused:
        return "refused"
    return "answered" if answer.grounded else "ungrounded"


def record_query(answer: Answer, seconds: float) -> None:
    route = answer.route or "docs"
    QUERIES.labels(route=route, outcome=_outcome(answer)).inc()
    LATENCY.labels(route=route).observe(seconds)
    FLAGGED_SOURCES.inc(len(answer.flagged_sources))

    model = answer.model
    if model is None:  # Refused before any model call; there is nothing billed to record.
        return
    TOKENS.labels(model=model, kind="input").inc(answer.usage.input_tokens)
    TOKENS.labels(model=model, kind="output").inc(answer.usage.output_tokens)
    TOKENS.labels(model=model, kind="cache_read").inc(answer.usage.cache_read_tokens)
    TOKENS.labels(model=model, kind="cache_write").inc(answer.usage.cache_write_tokens)
    if answer.cost_usd is None:
        # An unpriced model must show up as a gap, not as free.
        UNKNOWN_COST.inc()
    else:
        COST.labels(model=model).inc(float(answer.cost_usd or Decimal(0)))
