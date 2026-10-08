"""Bronze: stream new manifests, attach each object version's bytes, MERGE by event id.

`availableNow` processes everything registered since the last run and stops, so the job is a
batch container locally and a scheduled job on Databricks (where Auto Loader can replace the
file source) while keeping exactly-once progress in the checkpoint. The MERGE on `event_id`
makes a replayed micro-batch, or a manifest rewritten by backfill, a no-op.
"""

import logging
from functools import partial

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from ingestion.spark.objects import Credentials, fetch_contents, service_credentials
from ingestion.spark.schemas import BRONZE_OBJECTS_DDL, FETCHED_DDL, MANIFEST_DDL, column_names
from ingestion.spark.settings import Settings

log = logging.getLogger(__name__)


def ensure_table(spark: SparkSession, path: str, ddl: str) -> None:
    schema = spark.createDataFrame([], ddl).schema
    (
        DeltaTable.createIfNotExists(spark)
        .location(path)
        .addColumns(schema)
        # Silver reads bronze incrementally through the change feed.
        .property("delta.enableChangeDataFeed", "true")
        .execute()
    )


def merge_batch(batch: DataFrame, table_path: str, credentials: Credentials | None) -> None:
    spark = batch.sparkSession
    fetched = (
        # The file source adds partition columns (dt=...) from the manifest path; mapInPandas
        # matches output columns by name, so pass exactly the contract columns through.
        batch.select(*column_names(MANIFEST_DDL))
        .dropDuplicates(["event_id"])
        .mapInPandas(partial(fetch_contents, credentials=credentials), FETCHED_DDL)
        .withColumn("ingested_at", F.current_timestamp())
    )
    (
        DeltaTable.forPath(spark, table_path)
        .alias("t")
        .merge(fetched.alias("s"), "t.event_id = s.event_id")
        .whenNotMatchedInsertAll()
        .execute()
    )


def run(spark: SparkSession, settings: Settings) -> None:
    table_path = settings.table_path("bronze", "objects")
    ensure_table(spark, table_path, BRONZE_OBJECTS_DDL)
    # Resolved once, here in the job's own process. On serverless, foreachBatch runs in a
    # separate server-side Python process where `dbutils` cannot authenticate, so it cannot
    # resolve the credential itself. The keys last about an hour; an availableNow run over
    # the backlog takes minutes.
    credentials = (
        service_credentials(settings.service_credential, settings.region)
        if settings.service_credential
        else None
    )

    manifests = (
        spark.readStream.schema(MANIFEST_DDL)
        .option("maxFilesPerTrigger", settings.manifest_files_per_batch)
        .json(settings.manifests_path)
    )
    query = (
        manifests.writeStream.trigger(availableNow=True)
        .option("checkpointLocation", settings.checkpoint_path("bronze_objects"))
        .foreachBatch(lambda batch, _batch_id: merge_batch(batch, table_path, credentials))
        .start()
    )
    query.awaitTermination()
    log.info("bronze up to date: %s", table_path)
