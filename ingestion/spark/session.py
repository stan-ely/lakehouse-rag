from pyspark.sql import SparkSession

from ingestion.spark.settings import Settings


def build_session(settings: Settings, app_name: str = "lakehouse-rag-ingest") -> SparkSession:
    """Local sessions get Delta and S3A wiring; on Databricks the platform session is reused.

    Master and driver memory come from PYSPARK_SUBMIT_ARGS, because they must be fixed before
    the JVM starts and builder config is applied too late for them.
    """
    builder = SparkSession.builder.appName(app_name)
    if settings.s3_endpoint:
        conf = {
            # Delta jars are baked into the image instead of resolved from Maven at startup.
            "spark.sql.extensions": "io.delta.sql.DeltaSparkSessionExtension",
            "spark.sql.catalog.spark_catalog": "org.apache.spark.sql.delta.catalog.DeltaCatalog",
            "spark.sql.shuffle.partitions": str(settings.shuffle_partitions),
            "spark.hadoop.fs.s3a.endpoint": settings.s3_endpoint,
            "spark.hadoop.fs.s3a.endpoint.region": settings.region,
            "spark.hadoop.fs.s3a.path.style.access": "true",
            "spark.hadoop.fs.s3a.connection.ssl.enabled": str(
                settings.s3_endpoint.startswith("https")
            ).lower(),
            # Credentials come from AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY via S3A's default chain.
            "spark.ui.enabled": "false",
        }
        for key, value in conf.items():
            builder = builder.config(key, value)
    return builder.getOrCreate()
