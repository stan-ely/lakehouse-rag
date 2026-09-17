"""Run the golden set and gate on `eval/thresholds.yaml`.

    mise run eval                      # retrieval only: deterministic, no model, no cost
    mise run eval -- --mode full       # whole pipeline against the configured provider

`retrieval` scores what the index and the ACL filter do on their own, so it can gate CI with
the fake provider. `full` also routes, writes SQL and generates answers, so it needs a real
provider and costs money.

The harness calls the query service in-process rather than over HTTP: the API contract is
covered by its own tests, and this keeps every case on one stack with one set of pools.
"""

import argparse
import json
import logging
import os
import time
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.auth import Principal
from app.bootstrap import Stack, build_stack
from app.generation.answer import Answer
from app.settings import Settings, get_settings
from app.tokens import PERSONAS
from eval.golden import GoldenCase, build_golden_set
from eval.metrics import CaseResult, Summary, evaluate_case, failed_case, summarise

log = logging.getLogger("eval")

THRESHOLDS = Path(__file__).with_name("thresholds.yaml")
EXPERIMENT = "lakehouse-rag-eval"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--mode", choices=("retrieval", "full"), default="retrieval")
    parser.add_argument("--provider", choices=("fake", "bedrock", "anthropic"))
    parser.add_argument("--model", help="override the provider's default model")
    parser.add_argument("--kind", choices=("docs", "sql", "hybrid", "acl"), action="append")
    parser.add_argument("--limit", type=int, help="run only the first N cases (smoke runs)")
    parser.add_argument("--report", type=Path, default=Path("data/eval/report.json"))
    parser.add_argument("--thresholds", type=Path, default=THRESHOLDS)
    parser.add_argument("--no-mlflow", action="store_true", help="skip MLflow logging")
    parser.add_argument("--no-gate", action="store_true", help="report without failing on a miss")
    return parser.parse_args(argv)


def _settings(args: argparse.Namespace) -> Settings:
    overrides: dict[str, Any] = {}
    if args.provider:
        overrides["llm_provider"] = args.provider
    if args.model:
        overrides["llm_model"] = args.model
    if not overrides:
        return get_settings()
    return Settings(**{**get_settings().model_dump(), **overrides})


def _principal(case: GoldenCase) -> Principal:
    return Principal(f"eval-{case.persona}", frozenset(PERSONAS[case.persona]))


def _retrieval_only(stack: Stack, case: GoldenCase, principal: Principal, k: int) -> Answer:
    """Score the index alone: what a caller's groups can reach for this question."""
    if stack.retriever is None:
        raise RuntimeError("retrieval mode needs a retriever")
    chunks = stack.retriever.search(case.question, sorted(principal.groups), k)
    return Answer(
        text="",
        refused=not chunks,
        refusal_reason=None if chunks else "no_accessible_sources",
        citations=[],
        grounded=bool(chunks),
        retrieved=chunks,
        route=str(case.expected_route),
        cost_usd=Decimal(0),
    )


def run_cases(stack: Stack, cases: Sequence[GoldenCase], *, mode: str, k: int) -> list[CaseResult]:
    results: list[CaseResult] = []
    for n, case in enumerate(cases, start=1):
        principal = _principal(case)
        start = time.perf_counter()
        try:
            answer = (
                _retrieval_only(stack, case, principal, k)
                if mode == "retrieval"
                else stack.service.answer(case.question, principal)
            )
            result = evaluate_case(case, answer, (time.perf_counter() - start) * 1000)
        except Exception as exc:  # one bad case must not lose the whole run
            result = failed_case(case, f"{type(exc).__name__}: {exc}", 0.0)
            log.warning("%s raised %s", case.case_id, result.error)
        results.append(result)
        if n % 25 == 0 or n == len(cases):
            log.info("%d/%d cases", n, len(cases))
    return results


def check_thresholds(summary: Summary, profile: dict[str, dict[str, float]]) -> list[str]:
    failures = []
    for name, floor in (profile.get("min") or {}).items():
        value = summary.metrics.get(name)
        if value is None or value < floor:
            failures.append(f"{name} {value:.3f} < {floor}" if value is not None else name)
    for name, ceiling in (profile.get("max") or {}).items():
        value = summary.metrics.get(name)
        if value is None or value > ceiling:
            failures.append(f"{name} {value:.3f} > {ceiling}" if value is not None else name)
    return failures


def _serialise(result: CaseResult) -> dict[str, Any]:
    row = asdict(result)
    row["cost_usd"] = None if result.cost_usd is None else str(result.cost_usd)
    return row


def _log_to_mlflow(
    summary: Summary, args: argparse.Namespace, settings: Settings, report: Path
) -> None:
    import mlflow

    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"))
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=f"{args.mode}-{datetime.now(UTC):%Y%m%d-%H%M%S}"):
        mlflow.log_params(
            {
                "mode": args.mode,
                "provider": settings.llm_provider,
                "model": settings.llm_model or "default",
                "retrieval_k": settings.retrieval_k,
                "candidates": settings.retrieval_candidates,
                "rrf_k": settings.rrf_k,
                "min_similarity": settings.min_similarity,
                "cases": summary.cases,
            }
        )
        mlflow.log_metrics(summary.metrics)
        mlflow.log_artifact(str(report))


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    settings = _settings(args)

    cases = [c for c in build_golden_set() if not args.kind or c.kind in args.kind]
    cases = cases[: args.limit] if args.limit else cases
    if args.mode == "full" and settings.llm_provider == "fake":
        log.warning("full mode with the fake provider cannot generate answers or SQL")

    with build_stack(settings) as stack:
        results = run_cases(stack, cases, mode=args.mode, k=settings.retrieval_k)

    summary = summarise(results)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(
            {
                "mode": args.mode,
                "provider": settings.llm_provider,
                "model": settings.llm_model,
                "generated_at": datetime.now(UTC).isoformat(),
                "cases": summary.cases,
                "metrics": summary.metrics,
                "leaks": list(summary.leaks),
                "errors": list(summary.errors),
                "results": [_serialise(r) for r in results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    for name, value in summary.metrics.items():
        log.info("%-26s %.3f", name, value)
    log.info("report written to %s", args.report)

    if not args.no_mlflow:
        try:
            _log_to_mlflow(summary, args, settings, args.report)
        except Exception as exc:  # the gate must not depend on the tracking server
            log.warning("MLflow logging skipped: %s", exc)

    profile = yaml.safe_load(args.thresholds.read_text(encoding="utf-8"))[args.mode]
    failures = check_thresholds(summary, profile)
    if summary.leaks:
        log.error("ACL leaks in: %s", ", ".join(summary.leaks))
    if failures:
        log.error("thresholds not met: %s", "; ".join(failures))
        return 0 if args.no_gate else 1
    log.info("all %s thresholds met", args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
