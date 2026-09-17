from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from prometheus_client import REGISTRY

from app.generation.answer import Answer
from app.llm.base import Usage
from app.observability.metrics import record_query
from app.observability.tracing import configure_tracing, mask_span
from app.settings import Settings


def _raise(*args: object, **kwargs: object) -> None:
    raise RuntimeError("tracking server down")


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


def test_tracing_is_off_unless_the_setting_asks_for_it() -> None:
    import mlflow

    assert configure_tracing(Settings()) is False
    assert mlflow.tracing.provider.is_tracing_enabled() is False


def test_tracing_failures_never_break_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    import mlflow

    monkeypatch.setattr(mlflow, "set_experiment", _raise)

    assert configure_tracing(Settings(tracing_enabled=True)) is False


def test_spans_are_masked_before_export() -> None:
    span = SimpleNamespace(
        inputs={"question": "mail ana@larkspur.example about 4111 1111 1111 1111"},
        outputs=None,
    )
    span.set_inputs = lambda value: setattr(span, "inputs", value)
    span.set_outputs = lambda value: setattr(span, "outputs", value)

    mask_span(cast("Any", span))

    assert "ana@larkspur.example" not in span.inputs["question"]
    assert "[EMAIL]" in span.inputs["question"]
    assert "[CARD]" in span.inputs["question"]
