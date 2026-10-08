"""The AWS smoke runner's offline parts: its safety check, step selection and the report."""

from typing import Any

import pytest

from eval import smoke


def _report(**metrics: float) -> dict[str, Any]:
    return {
        "generated_at": "2026-09-24T10:00:00+00:00",
        "model": smoke.MODEL,
        "cases": 121,
        # run_eval writes the case ids, not counts.
        "leaks": [],
        "errors": [],
        "metrics": metrics,
    }


def test_refuses_to_run_against_floci(monkeypatch: pytest.MonkeyPatch) -> None:
    # A leftover endpoint would seed the emulator and report a cloud success that never was.
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")

    with pytest.raises(SystemExit, match="MISE_ENV=aws"):
        smoke.main([])


def test_resume_runs_the_named_step_and_everything_after(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    ran: list[list[str]] = []

    class Recorder:
        state: dict[str, Any] = {}  # noqa: RUF012 - a stand-in, never mutated

        def __init__(self, args: Any) -> None:
            pass

        def execute(self, names: list[str]) -> None:
            ran.append(names)

    monkeypatch.setattr(smoke, "Smoke", Recorder)

    smoke.main(["--from", "index"])
    smoke.main(["--only", "report"])

    assert ran == [["index", "eval", "report"], ["report"]]


def test_report_compares_against_the_local_baseline() -> None:
    state = {
        "raw_objects": 180,
        "manifests": 180,
        "chunks": 950,
        "documents": 175,
        "gate_passed": True,
        "seconds": {"seed": 12.5},
    }

    text = smoke.render(
        _report(router_accuracy=0.93, acl_leaks=0.0, p50_latency_ms=1800.0),
        _report(router_accuracy=0.9266, acl_leaks=0.0, p50_latency_ms=1666.6),
        state,
    )

    assert "| router_accuracy | 0.9300 | 0.9266 |" in text
    assert "| p50_latency_ms | 1,800 | 1,667 |" in text
    assert "| Threshold gate | passed |" in text
    assert "| Manifests written by the Lambda | 180 |" in text
    assert "| seed | 12.5 |" in text


def test_report_without_a_baseline_has_one_value_column() -> None:
    text = smoke.render(_report(router_accuracy=0.93), None, {})

    assert "| router_accuracy | 0.9300 |\n" in text
    assert "| Threshold gate | not run |" in text


def test_report_counts_leaked_and_failed_cases() -> None:
    report = _report(router_accuracy=0.93) | {"leaks": ["acl-03", "acl-07"], "errors": ["sql-12"]}

    text = smoke.render(report, None, {})

    assert "| ACL leaks | 2 |\n" in text
    assert "| Errors | 1 |\n" in text
