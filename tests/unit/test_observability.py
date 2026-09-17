from decimal import Decimal

from prometheus_client import REGISTRY

from app.generation.answer import Answer
from app.llm.base import Usage
from app.observability.metrics import record_query


def _value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _answer(**overrides: object) -> Answer:
    fields: dict[str, object] = {
        "text": "Cap is $180 [1].",
        "refused": False,
        "refusal_reason": None,
        "citations": [],
        "grounded": True,
        "retrieved": [],
        "route": "docs",
        "model": "amazon.nova-lite",
        "usage": Usage(input_tokens=100, output_tokens=20),
        "cost_usd": Decimal("0.00012"),
    }
    return Answer(**{**fields, **overrides})  # type: ignore[arg-type]


def test_a_served_query_increments_count_tokens_and_cost() -> None:
    before = _value("rag_llm_cost_usd_total", model="amazon.nova-lite")

    record_query(_answer(), seconds=1.5)

    assert _value("rag_queries_total", route="docs", outcome="answered") >= 1
    assert _value("rag_llm_tokens_total", model="amazon.nova-lite", kind="input") >= 100
    assert _value("rag_llm_cost_usd_total", model="amazon.nova-lite") > before


def test_a_refusal_before_any_model_call_records_no_spend() -> None:
    before = _value("rag_unknown_cost_total")

    record_query(
        _answer(refused=True, refusal_reason="low_relevance", grounded=False, model=None),
        seconds=0.02,
    )

    assert _value("rag_queries_total", route="docs", outcome="refused") >= 1
    assert _value("rag_unknown_cost_total") == before, "no model ran, so nothing is unpriced"


def test_an_unpriced_model_is_counted_as_a_gap_not_as_free() -> None:
    before = _value("rag_unknown_cost_total")

    record_query(_answer(model="some-new-model", cost_usd=None), seconds=1.0)

    assert _value("rag_unknown_cost_total") == before + 1
    assert _value("rag_llm_cost_usd_total", model="some-new-model") == 0.0


def test_flagged_sources_are_counted() -> None:
    before = _value("rag_flagged_sources_total")

    record_query(_answer(flagged_sources=("tck-13:0000", "tck-99:0001")), seconds=1.0)

    assert _value("rag_flagged_sources_total") == before + 2
