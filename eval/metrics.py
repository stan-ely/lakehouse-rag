"""Scoring for one golden case, and the aggregate metrics the CI gate reads.

Answers are compared after normalisation: currency symbols, thousands separators and repeated
whitespace are removed, so "$1,234" and "1234" match. Numeric facts must match on a word
boundary, so "5" does not match "25".
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.generation.answer import Answer
from eval.golden import GoldenCase

_STRIP = re.compile(r"[$,]")
_SPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    return _SPACE.sub(" ", _STRIP.sub("", text.lower())).strip()


# English prose spells out small numbers, so an answer can be correct and still never contain
# the digit: "There are three people in Executive" for an expected fact of "3". Scoring that as a
# miss measures the model's prose style, not whether it got the number right. Twenty is where
# spelling out stops being the normal choice.
_NUMBER_WORDS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
)


def _digit_forms(needle: str) -> list[str]:
    """The needle plus its English word, when it is a small whole number."""
    forms = [needle]
    if needle.isdigit() and int(needle) < len(_NUMBER_WORDS):
        forms.append(_NUMBER_WORDS[int(needle)])
    return forms


def contains(haystack: str, needle: str) -> bool:
    """Substring match, except that a purely numeric needle must stand as its own token."""
    needle = normalise(needle)
    if re.fullmatch(r"[\d.]+", needle):
        # A trailing full stop is sentence punctuation; a trailing ".5" is a different number.
        return any(
            re.search(rf"(?<![\d.\w]){re.escape(form)}(?!\d)(?!\.\d)\b", haystack)
            for form in _digit_forms(needle)
        )
    return needle in haystack


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    kind: str
    expected_route: str
    route: str | None
    retrieved_docs: tuple[str, ...]
    hit_rank: int | None
    refused: bool
    refusal_reason: str | None
    grounded: bool
    citations: int
    facts_found: int
    facts_expected: int
    leaked: tuple[str, ...]
    sql: str | None
    sql_error: str | None
    latency_ms: float
    cost_usd: Decimal | None
    error: str | None = None

    @property
    def retrieval_hit(self) -> bool:
        return self.hit_rank is not None

    @property
    def correct(self) -> bool:
        return not self.refused and self.facts_found == self.facts_expected

    @property
    def route_correct(self) -> bool:
        return self.route == self.expected_route


def evaluate_case(case: GoldenCase, answer: Answer, latency_ms: float) -> CaseResult:
    seen: list[str] = []
    for chunk in answer.retrieved:
        if chunk.doc_id not in seen:
            seen.append(chunk.doc_id)
    hit_rank = next(
        (i for i, doc_id in enumerate(seen, start=1) if doc_id in case.expected_docs), None
    )

    text = normalise(answer.text)
    context = normalise(" ".join(chunk.content for chunk in answer.retrieved))
    facts_found = sum(1 for fact in case.expected_facts if contains(text, fact))
    leaked = tuple(f for f in case.forbidden if contains(text, f) or contains(context, f))
    return CaseResult(
        case_id=case.case_id,
        kind=case.kind,
        expected_route=str(case.expected_route),
        route=answer.route,
        retrieved_docs=tuple(seen),
        hit_rank=hit_rank,
        refused=answer.refused,
        refusal_reason=answer.refusal_reason,
        grounded=answer.grounded,
        citations=len(answer.citations),
        facts_found=facts_found,
        facts_expected=len(case.expected_facts),
        leaked=leaked,
        sql=answer.sql,
        sql_error=answer.sql_error,
        latency_ms=latency_ms,
        cost_usd=answer.cost_usd,
    )


def failed_case(case: GoldenCase, error: str, latency_ms: float) -> CaseResult:
    """A case whose request raised: scored as a total miss rather than dropped from the run."""
    return CaseResult(
        case_id=case.case_id,
        kind=case.kind,
        expected_route=str(case.expected_route),
        route=None,
        retrieved_docs=(),
        hit_rank=None,
        refused=False,
        refusal_reason=None,
        grounded=False,
        citations=0,
        facts_found=0,
        facts_expected=len(case.expected_facts),
        leaked=(),
        sql=None,
        sql_error=None,
        latency_ms=latency_ms,
        cost_usd=None,
        error=error,
    )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True)
class Summary:
    cases: int
    metrics: dict[str, float]
    # Metrics whose denominator was non-empty. A filtered run (say, ACL cases only) measures
    # nothing about retrieval, and the gate must not read that as a score of zero.
    measured: frozenset[str] = frozenset()
    leaks: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)


def summarise(results: Sequence[CaseResult]) -> Summary:
    """Aggregates one run.

    In full mode `recall_at_k` is end to end, not a pure index score: a question the router
    sends to SQL alone never retrieves documents, so bad routing lowers it. The retrieval
    profile measures the index on its own.
    """
    answerable = [r for r in results if r.kind != "acl"]
    with_docs = [r for r in answerable if r.kind in ("docs", "hybrid")]
    acl = [r for r in results if r.kind == "acl"]
    sql_cases = [r for r in answerable if r.kind in ("sql", "hybrid")]
    answered = [r for r in answerable if not r.refused]

    metrics = {
        "recall_at_k": _ratio(sum(r.retrieval_hit for r in with_docs), len(with_docs)),
        "mrr": sum(1 / r.hit_rank for r in with_docs if r.hit_rank) / len(with_docs)
        if with_docs
        else 0.0,
        "router_accuracy": _ratio(sum(r.route_correct for r in answerable), len(answerable)),
        "answer_correctness": _ratio(sum(r.correct for r in answerable), len(answerable)),
        "sql_success": _ratio(
            sum(r.sql is not None and r.sql_error is None for r in sql_cases), len(sql_cases)
        ),
        "refusal_rate_restricted": _ratio(sum(r.refused for r in acl), len(acl)),
        "grounded_rate": _ratio(sum(r.grounded for r in answered), len(answered)),
        "unexpected_refusal_rate": _ratio(sum(r.refused for r in answerable), len(answerable)),
        "acl_leaks": float(sum(len(r.leaked) for r in results)),
        "errors": float(sum(r.error is not None for r in results)),
        "p50_latency_ms": _percentile([r.latency_ms for r in results], 0.50),
        "p95_latency_ms": _percentile([r.latency_ms for r in results], 0.95),
        "total_cost_usd": float(sum((r.cost_usd or Decimal(0) for r in results), Decimal(0))),
    }
    populations = {
        "recall_at_k": with_docs,
        "mrr": with_docs,
        "router_accuracy": answerable,
        "answer_correctness": answerable,
        "sql_success": sql_cases,
        "refusal_rate_restricted": acl,
        "grounded_rate": answered,
        "unexpected_refusal_rate": answerable,
    }
    measured = {name for name in metrics if populations.get(name, results)}
    return Summary(
        cases=len(results),
        metrics=metrics,
        measured=frozenset(measured),
        leaks=tuple(r.case_id for r in results if r.leaked),
        errors=tuple(r.case_id for r in results if r.error),
    )


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(q * (len(ordered) - 1)))
    return ordered[index]
