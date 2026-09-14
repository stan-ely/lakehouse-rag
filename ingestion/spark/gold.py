"""Gold: retrieval chunks for live, canonical documents, reconciled per changed key.

Silver rows are updated in place, so gold streams silver's Change Data Feed. For each batch it
takes the keys that changed, reads their *current* silver state, and reconciles all of those
keys' chunks in one MERGE: new chunks are inserted, changed ones (by `row_hash`) updated, and
chunks that should no longer exist deleted: the document was deleted, failed to parse, became
a duplicate, or simply got shorter. Unchanged chunks are not rewritten, so gold's own change
feed carries only real changes to the indexer.
"""

import logging

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from ingestion.spark.bronze import ensure_table
from ingestion.spark.chunks import CHUNK_COLUMNS, chunk_rows
from ingestion.spark.schemas import CHUNKS_DDL, GOLD_CHUNKS_DDL
from ingestion.spark.settings import Settings

log = logging.getLogger(__name__)


def desired_chunks(documents: DataFrame) -> DataFrame:
    live = documents.filter(
        ~F.col("is_deleted") & F.col("parse_error").isNull() & ~F.col("is_duplicate")
    )
    return live.mapInPandas(chunk_rows, CHUNKS_DDL)


def reconcile(spark: SparkSession, silver_path: str, gold_path: str, keys: DataFrame) -> None:
    current = DeltaTable.forPath(spark, silver_path).toDF().join(keys, "key")
    desired = desired_chunks(current).withColumn("_op", F.lit("upsert")).localCheckpoint()

    target = DeltaTable.forPath(spark, gold_path)
    stale = (
        target.toDF()
        .join(keys, "key")
        .select("chunk_id")
        .join(desired.select("chunk_id"), "chunk_id", "left_anti")
    )
    tombstones = desired.limit(0).unionByName(
        stale.withColumn("_op", F.lit("delete")), allowMissingColumns=True
    )
    changes = desired.unionByName(tombstones)

    (
        target.alias("t")
        .merge(changes.alias("s"), "t.chunk_id = s.chunk_id")
        .whenMatchedDelete(condition="s._op = 'delete'")
        .whenMatchedUpdate(
            condition="s._op = 'upsert' AND t.row_hash <> s.row_hash",
            set={**{c: f"s.{c}" for c in CHUNK_COLUMNS}, "updated_at": "current_timestamp()"},
        )
        .whenNotMatchedInsert(
            condition="s._op = 'upsert'",
            values={**{c: f"s.{c}" for c in CHUNK_COLUMNS}, "updated_at": "current_timestamp()"},
        )
        .execute()
    )


def run(spark: SparkSession, settings: Settings) -> None:
    silver_path = settings.table_path("silver", "documents")
    gold_path = settings.table_path("gold", "chunks")
    ensure_table(spark, gold_path, GOLD_CHUNKS_DDL)

    def process(batch: DataFrame, _batch_id: int) -> None:
        keys = batch.filter("_change_type != 'update_preimage'").select("key").distinct()
        reconcile(spark, silver_path, gold_path, keys)

    query = (
        spark.readStream.format("delta")
        .option("readChangeFeed", "true")
        .load(silver_path)
        .writeStream.trigger(availableNow=True)
        .option("checkpointLocation", settings.checkpoint_path("gold_chunks"))
        .foreachBatch(process)
        .start()
    )
    query.awaitTermination()
    log.info("gold up to date: %s", gold_path)
