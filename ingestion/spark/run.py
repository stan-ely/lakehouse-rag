"""Entry point for the ingest container and the Databricks job: `python -m ingestion.spark.run`."""

import argparse
import logging
from collections.abc import Callable

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
    parser.add_argument("steps", nargs="*", choices=list(STEPS), default=list(STEPS))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = Settings.from_env()
    spark = build_session(settings)
    spark.sparkContext.setLogLevel("WARN")
    try:
        for step in args.steps:
            STEPS[step](spark, settings)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
