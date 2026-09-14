# One-shot ingest image: Spark 4.2 (PySpark) + Delta 4.4 on JDK 21 and Python 3.12.3,
# matching Databricks Runtime 19 / serverless environment 6.
FROM eclipse-temurin:21-jre-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# JVM dependencies PySpark does not ship, checksum-verified. First so the large, rarely
# changing layer (the AWS SDK bundle is ~690 MB) survives Python dependency changes.
COPY docker/spark/jars.sha1 /tmp/jars.sha1
RUN set -eu; mkdir -p /opt/spark-jars; cd /opt/spark-jars; \
    while read -r sha path; do \
      [ -n "$sha" ] || continue; \
      file=$(basename "$path"); \
      curl -fsSL --retry 3 -o "$file" "https://repo1.maven.org/maven2/$path"; \
      echo "$sha  $file" | sha1sum -c -; \
    done < /tmp/jars.sha1

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# --no-cache: pyspark ships as an sdist, so uv's cache would keep the sdist and the built wheel
# (~3 GB) in this layer next to the installed copy.
RUN uv sync --frozen --only-group ingest --no-install-project --no-cache

COPY ingestion ./ingestion

RUN useradd --create-home --uid 10001 spark
USER spark

# Master and driver memory must be set before the JVM starts. Executors share the driver JVM
# in local mode, so the driver classpath is enough for S3A and Delta.
ENV PYSPARK_SUBMIT_ARGS="--master local[2] --driver-memory 1g --driver-class-path /opt/spark-jars/* pyspark-shell"

ENTRYPOINT ["python", "-m", "ingestion.spark.run"]
