"""The Databricks job and the local container share this entry point, so its arguments matter."""

from typing import Any

import pytest
from pyspark.sql import SparkSession

from ingestion.spark import run
from ingestion.spark.settings import Settings


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Settings]]:
    calls: list[tuple[str, Settings]] = []

    def step(name: str) -> Any:
        def inner(spark: SparkSession, settings: Settings) -> None:
            calls.append((name, settings))

        return inner

    monkeypatch.setattr(run, "STEPS", {name: step(name) for name in ("bronze", "silver", "gold")})
    monkeypatch.setattr(run, "build_session", lambda settings: _FakeSession())
    return calls


class _FakeSession:
    class _Context:
        def setLogLevel(self, level: str) -> None:  # Spark's own casing.
            pass

    sparkContext = _Context()

    def stop(self) -> None:
        pass


def test_no_steps_runs_the_whole_medallion_in_order(recorded: list[tuple[str, Settings]]) -> None:
    run.main([])

    assert [name for name, _ in recorded] == ["bronze", "silver", "gold"]


def test_lake_root_overrides_the_environment(
    recorded: list[tuple[str, Settings]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The serverless task has no shell to export RAG_LAKE_ROOT in, so it passes a parameter.
    monkeypatch.setenv("RAG_LAKE_ROOT", "s3a://larkspur-local-lake")

    run.main(["gold", "--lake-root", "s3://larkspur-smoke-lake/"])

    (name, settings) = recorded[0]
    assert name == "gold"
    assert settings.lake_root == "s3://larkspur-smoke-lake", "the trailing slash is stripped"
    assert settings.table_path("gold", "chunks") == "s3://larkspur-smoke-lake/delta/gold/chunks"


def test_without_the_flag_the_environment_still_decides(
    recorded: list[tuple[str, Settings]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_LAKE_ROOT", "s3a://larkspur-local-lake")

    run.main(["bronze"])

    assert recorded[0][1].lake_root == "s3a://larkspur-local-lake"


def test_an_unknown_step_is_rejected(recorded: list[tuple[str, Settings]]) -> None:
    with pytest.raises(SystemExit):
        run.main(["platinum"])

    assert recorded == []
