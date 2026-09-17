"""Entry point for the ingest container and the Databricks job: `python -m ingestion.spark.run`."""

import argparse
import logging
from collections.abc import Callable
from dataclasses import replace

from pyspark.sql import SparkSession

from ingestion.spark import bronze, gold, silver
from ingestion.spark.session import build_session
from ingestion.spark.settings import Settings

STEPS: dict[str, Callable[[SparkSession, Settings], None]] = {
    "bronze": bronze.run,
    "silver": silver.run,
    "gold": gold.run,
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run medallion ingestion steps in order")
    # `choices` is checked against the default too when nargs="*" and no steps are given, so a
    # list default fails its own validation. The membership check below does the same job.
    parser.add_argument("steps", nargs="*", default=list(STEPS), metavar=f"{{{','.join(STEPS)}}}")
    # The Databricks job passes the lake root as a parameter: a serverless task has no shell to
    # export RAG_LAKE_ROOT in, and the bundle should not depend on a cluster-level env var.
    parser.add_argument("--lake-root", help="overrides RAG_LAKE_ROOT")
    args = parser.parse_args(argv)
    if unknown := [step for step in args.steps if step not in STEPS]:
        parser.error(f"unknown step(s): {', '.join(unknown)}; choose from {', '.join(STEPS)}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = Settings.from_env()
    if args.lake_root:
        settings = replace(settings, lake_root=args.lake_root.rstrip("/"))
    spark = build_session(settings)
    spark.sparkContext.setLogLevel("WARN")
    try:
        for step in args.steps:
            STEPS[step](spark, settings)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
