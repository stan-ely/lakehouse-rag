"""Entry point for the ingest container and the Databricks job: `python -m ingestion.spark.run`."""

import argparse
import logging
from collections.abc import Callable
from dataclasses import replace

from pyspark.sql import SparkSession
from pyspark.sql.utils import is_remote

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
    # Databricks only: names the UC service credential bronze reads raw objects with.
    parser.add_argument("--service-credential", help="overrides RAG_SERVICE_CREDENTIAL")
    args = parser.parse_args(argv)
    if unknown := [step for step in args.steps if step not in STEPS]:
        parser.error(f"unknown step(s): {', '.join(unknown)}; choose from {', '.join(STEPS)}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = Settings.from_env()
    if args.lake_root:
        settings = replace(settings, lake_root=args.lake_root.rstrip("/"))
    if args.service_credential:
        settings = replace(settings, service_credential=args.service_credential)
    spark = build_session(settings)
    # Serverless runs over Spark Connect, which has no SparkContext; the platform owns logging.
    if not is_remote():
        spark.sparkContext.setLogLevel("WARN")
    try:
        for step in args.steps:
            STEPS[step](spark, settings)
    finally:
        # On serverless the platform owns the session. Stopping it after `dbutils` has resolved
        # a service credential never returns, and the task hangs after its work is done.
        if not is_remote():
            spark.stop()


if __name__ == "__main__":
    main()
