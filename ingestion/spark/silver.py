"""Silver: latest parsed state per object key, with duplicate detection.

Bronze is insert-only, so silver streams it directly (`availableNow`) and per micro-batch:

1. keeps each key's newest event (`sort_key`), so out-of-order registration cannot regress;
2. parses in `mapInPandas` (errors become rows, see `documents.py`);
3. MERGEs by key, updating only when the incoming event is newer;
4. recomputes duplicate groups for the fingerprints this batch touched, including fingerprints
   the changed keys had *before* the batch, so deleting or editing a canonical copy promotes
   the next one.

Duplicates are only collapsed within identical `allowed_groups`: an HR-only copy of a public
page must stay a separate, separately-protected document.
"""

import logging

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from ingestion.spark.bronze import ensure_table
from ingestion.spark.documents import parse_documents
from ingestion.spark.schemas import PARSED_DDL, SILVER_DOCUMENTS_DDL
from ingestion.spark.settings import Settings

log = logging.getLogger(__name__)

_BRONZE_INPUT = [
    "key",
    "bucket",
    "event_id",
    "event_type",
    "version_id",
    "sort_key",
    "doc_id",
    "source_type",
    "title",
    "owner",
    "allowed_groups",
    "source_updated_at",
    "content",
    "content_sha256",
    "fetch_error",
]


def latest_per_key(batch: DataFrame) -> DataFrame:
    newest_first = Window.partitionBy("key").orderBy(F.desc("sort_key"), F.desc("event_id"))
    return (
        batch.withColumn("_rank", F.row_number().over(newest_first))
        .filter("_rank = 1")
        .drop("_rank")
    )


def canonical_keys(documents: DataFrame) -> DataFrame:
    """(key, canonical_key) for live documents: the oldest copy wins, then the shortest key."""
    group = Window.partitionBy("fingerprint", F.array_join("allowed_groups", ",")).orderBy(
        F.asc_nulls_last("source_updated_at"), F.length("key"), "key"
    )
    live = documents.filter(~F.col("is_deleted") & F.col("parse_error").isNull())
    return live.select("key", F.first("key").over(group).alias("canonical_key"))


def refresh_duplicates(spark: SparkSession, table_path: str, fingerprints: list[str]) -> None:
    if not fingerprints:
        return
    target = DeltaTable.forPath(spark, table_path)
    affected = target.toDF().filter(F.col("fingerprint").isin(fingerprints))
    updates = (
        affected.select("key")
        .join(canonical_keys(affected), "key", "left")
        .select("key", F.coalesce("canonical_key", "key").alias("canonical_key"))
    )
    (
        target.alias("t")
        .merge(updates.alias("s"), "t.key = s.key")
        .whenMatchedUpdate(
            condition="NOT (t.canonical_key <=> s.canonical_key)",
            set={
                "canonical_key": "s.canonical_key",
                "is_duplicate": "s.canonical_key <> t.key",
                "updated_at": "current_timestamp()",
            },
        )
        .execute()
    )


def merge_batch(batch: DataFrame, table_path: str) -> None:
    spark = batch.sparkSession
    documents = (
        latest_per_key(batch.select(*_BRONZE_INPUT))
        .mapInPandas(parse_documents, PARSED_DDL)
        # Provisional: every document is its own canonical copy until refresh_duplicates runs.
        .withColumn("canonical_key", F.col("key"))
        .withColumn("is_duplicate", F.lit(False))
        .withColumn("updated_at", F.current_timestamp())
        .localCheckpoint()  # parse once: the rows feed both the fingerprint scan and the MERGE
    )
    target = DeltaTable.forPath(spark, table_path)
    fingerprints = {
        row.fingerprint
        for frame in (
            documents.select("fingerprint"),
            target.toDF().join(documents.select("key"), "key").select("fingerprint"),
        )
        for row in frame.filter(F.col("fingerprint").isNotNull()).distinct().collect()
    }
    (
        target.alias("t")
        .merge(documents.alias("s"), "t.key = s.key")
        .whenMatchedUpdateAll(condition="s.sort_key > t.sort_key")
        .whenNotMatchedInsertAll()
        .execute()
    )
    refresh_duplicates(spark, table_path, sorted(fingerprints))


def run(spark: SparkSession, settings: Settings) -> None:
    table_path = settings.table_path("silver", "documents")
    ensure_table(spark, table_path, SILVER_DOCUMENTS_DDL)
    query = (
        spark.readStream.format("delta")
        .load(settings.table_path("bronze", "objects"))
        .writeStream.trigger(availableNow=True)
        .option("checkpointLocation", settings.checkpoint_path("silver_documents"))
        .foreachBatch(lambda batch, _batch_id: merge_batch(batch, table_path))
        .start()
    )
    query.awaitTermination()
    log.info("silver up to date: %s", table_path)
