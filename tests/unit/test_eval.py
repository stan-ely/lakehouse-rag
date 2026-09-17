"""The golden set must stay consistent with the corpus, and the metrics must score it honestly."""

from decimal import Decimal

import pytest

from app.generation.answer import Answer
from app.router.classifier import Route
from app.tokens import PERSONAS
from data_gen.corpus import build_corpus
from data_gen.world import build_world
from eval.golden import SEED, GoldenCase, build_golden_set
from eval.metrics import contains, evaluate_case, failed_case, normalise, summarise
from eval.run_eval import check_thresholds
from tests.unit.factories import make_chunk


@pytest.fixture(scope="module")
def cases() -> tuple[GoldenCase, ...]:
    return build_golden_set()


@pytest.fixture(scope="module")
def corpus_doc_ids() -> set[str]:
    world = build_world(SEED)
    return {doc.doc_id for doc in build_corpus(world, SEED)}


def _answer(**overrides: object) -> Answer:
    values: dict[str, object] = {
        "text": "",
        "refused": False,
        "refusal_reason": None,
        "citations": [],
        "grounded": True,
        "retrieved": [],
        "route": "docs",
        "cost_usd": Decimal(0),
    }
    return Answer(**{**values, **overrides})  # type: ignore[arg-type]


def test_every_expected_document_exists_in_the_corpus(
    cases: tuple[GoldenCase, ...], corpus_doc_ids: set[str]
) -> None:
    expected = {doc_id for case in cases for doc_id in case.expected_docs}
    assert expected <= corpus_doc_ids


def test_every_persona_is_one_the_token_minter_knows(cases: tuple[GoldenCase, ...]) -> None:
    assert {case.persona for case in cases} <= set(PERSONAS)


def test_case_ids_are_unique_and_every_kind_is_covered(cases: tuple[GoldenCase, ...]) -> None:
    assert len({case.case_id for case in cases}) == len(cases)
    assert {case.kind for case in cases} == {"docs", "sql", "hybrid", "acl"}


def test_answerable_cases_carry_expectations(cases: tuple[GoldenCase, ...]) -> None:
    for case in cases:
        if case.must_refuse:
            assert not case.expected_facts, case.case_id
        else:
            assert case.expected_facts, case.case_id


def test_document_cases_name_the_document_that_should_answer_them(
    cases: tuple[GoldenCase, ...],
) -> None:
    for case in cases:
        if case.expected_route is Route.DOCS and not case.must_refuse:
            assert case.expected_docs, case.case_id


def test_the_set_is_deterministic() -> None:
    assert build_golden_set() == build_golden_set()


def test_normalise_strips_currency_and_separators() -> None:
    assert normalise("  $1,234.50  per   night ") == "1234.50 per night"


def test_numeric_facts_must_match_a_whole_number() -> None:
    assert contains("the cap is 250 a night", "250")
    assert not contains("the cap is 250 a night", "50")
    assert not contains("we credit 10%", "1")


def test_text_facts_match_as_substrings() -> None:
    assert contains("escalate to the director of customer support", "director of customer support")


def test_a_case_scores_the_rank_of_the_first_expected_document() -> None:
    case = GoldenCase("c1", "q", "sales", Route.DOCS, ("doc-2",), ("7",))
    answer = _answer(
        text="It is 7 days [1].",
        retrieved=[make_chunk(1), make_chunk(2), make_chunk(2)],
    )
    result = evaluate_case(case, answer, 12.0)

    assert result.hit_rank == 2
    assert result.retrieved_docs == ("doc-1", "doc-2")
    assert result.correct


def test_a_forbidden_string_in_the_context_counts_as_a_leak() -> None:
    case = GoldenCase("acl-1", "q", "sales", Route.DOCS, forbidden=("320000",), must_refuse=True)
    answer = _answer(
        text="I don't have enough information.",
        refused=True,
        retrieved=[make_chunk(1, content="Band L7 tops out at $320,000.")],
    )
    result = evaluate_case(case, answer, 1.0)

    assert result.leaked == ("320000",)
    assert summarise([result]).metrics["acl_leaks"] == 1.0


def test_summary_counts_refusals_routes_and_sql() -> None:
    docs = evaluate_case(
        GoldenCase("d1", "q", "sales", Route.DOCS, ("doc-1",), ("1",)),
        _answer(text="the answer is 1 [1]", retrieved=[make_chunk(1)]),
        10.0,
    )
    sql = evaluate_case(
        GoldenCase("s1", "q", "sales", Route.SQL, expected_facts=("42",)),
        _answer(text="43", route="sql", sql="SELECT 1", retrieved=[make_chunk(2)]),
        20.0,
    )
    acl = evaluate_case(
        GoldenCase("a1", "q", "sales", Route.DOCS, forbidden=("secret",), must_refuse=True),
        _answer(text="I don't have enough information.", refused=True, grounded=False),
        5.0,
    )
    summary = summarise([docs, sql, acl])

    assert summary.cases == 3
    assert summary.metrics["recall_at_k"] == 1.0
    assert summary.metrics["answer_correctness"] == 0.5
    assert summary.metrics["sql_success"] == 1.0
    assert summary.metrics["refusal_rate_restricted"] == 1.0
    assert summary.metrics["acl_leaks"] == 0.0
    assert summary.metrics["p50_latency_ms"] == 10.0


def test_a_raising_case_is_scored_as_a_miss_not_dropped() -> None:
    case = GoldenCase("d1", "q", "sales", Route.DOCS, ("doc-1",), ("1",))
    summary = summarise([failed_case(case, "RuntimeError: pool closed", 0.0)])

    assert summary.errors == ("d1",)
    assert summary.metrics["errors"] == 1.0
    assert summary.metrics["recall_at_k"] == 0.0


def test_thresholds_report_every_metric_that_misses() -> None:
    summary = summarise(
        [
            evaluate_case(
                GoldenCase("d1", "q", "sales", Route.DOCS, ("doc-9",), ("1",)),
                _answer(text="no idea", retrieved=[make_chunk(1)]),
                1.0,
            )
        ]
    )
    failures = check_thresholds(summary, {"min": {"recall_at_k": 0.9}, "max": {"acl_leaks": 0}})

    assert failures == ["recall_at_k 0.000 < 0.9"]
