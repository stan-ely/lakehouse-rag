"""The evaluation harness against the real index: it must score cases and never leak.

Runs the retrieval profile on a subset, which needs no model and no MLflow. The full profile is
a manual run, because it needs a real provider.
"""

from collections.abc import Iterator

import pytest

from app.bootstrap import Stack, build_stack
from app.settings import Settings
from eval.golden import GoldenCase, build_golden_set
from eval.metrics import summarise
from eval.run_eval import RETRIEVAL_METRICS, check_thresholds, for_mode, run_cases

pytestmark = pytest.mark.integration

SETTINGS = Settings(llm_provider="fake")
SUBSET = 12


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    with build_stack(SETTINGS) as built:
        yield built


def _cases(kind: str, limit: int = SUBSET) -> list[GoldenCase]:
    return [case for case in build_golden_set() if case.kind == kind][:limit]


def test_document_cases_retrieve_the_document_that_answers_them(stack: Stack) -> None:
    results = run_cases(stack, _cases("docs"), mode="retrieval", k=SETTINGS.retrieval_k)
    summary = for_mode(summarise(results), "retrieval")

    assert set(summary.metrics) == RETRIEVAL_METRICS
    assert summary.errors == ()
    misses = [r.case_id for r in results if not r.retrieval_hit]
    assert summary.metrics["recall_at_k"] >= 0.9, f"missed {misses}"


def test_restricted_cases_never_put_forbidden_text_in_reach(stack: Stack) -> None:
    results = run_cases(stack, _cases("acl"), mode="retrieval", k=SETTINGS.retrieval_k)
    summary = summarise(results)

    assert summary.leaks == ()
    assert summary.metrics["acl_leaks"] == 0.0


def test_the_gate_fails_when_a_threshold_is_missed(stack: Stack) -> None:
    results = run_cases(stack, _cases("docs", 4), mode="retrieval", k=SETTINGS.retrieval_k)
    summary = for_mode(summarise(results), "retrieval")

    assert check_thresholds(summary, {"min": {"recall_at_k": 0.9}, "max": {"acl_leaks": 0}}) == []
    assert check_thresholds(summary, {"min": {"recall_at_k": 1.01}, "max": {}}) != []
