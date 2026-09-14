"""Table contracts as DDL, shared by the jobs and their tests."""

# Mirrors `ingestion.lambda_register.handler.Manifest`; unit tests keep the two in sync.
MANIFEST_DDL = ", ".join(
    [
        "manifest_version INT",
        "event_id STRING",
        "event_type STRING",
        "event_name STRING",
        "bucket STRING",
        "key STRING",
        "version_id STRING",
        "etag STRING",
        "size BIGINT",
        "event_time STRING",
        "sequencer STRING",
        "sort_key STRING",
        "content_type STRING",
        "doc_id STRING",
        "source_type STRING",
        "title STRING",
        "owner STRING",
        "allowed_groups ARRAY<STRING>",
        "source_updated_at STRING",
        "metadata MAP<STRING, STRING>",
        "registered_at STRING",
    ]
)

FETCHED_DDL = f"{MANIFEST_DDL}, content BINARY, content_sha256 STRING, fetch_error STRING"

# One row per registered event: the replayable history of every object version and deletion.
BRONZE_OBJECTS_DDL = f"{FETCHED_DDL}, ingested_at TIMESTAMP"

# Output of `ingestion.spark.documents.parse_documents`: one parsed state per object event.
PARSED_DDL = ", ".join(
    [
        "key STRING",
        "bucket STRING",
        "event_id STRING",
        "version_id STRING",
        "sort_key STRING",
        "is_deleted BOOLEAN",
        "doc_id STRING",
        "source_type STRING",
        "title STRING",
        "owner STRING",
        "allowed_groups ARRAY<STRING>",
        "source_updated_at STRING",
        "content_sha256 STRING",
        "fingerprint STRING",
        "sections ARRAY<STRUCT<heading: STRING, text: STRING>>",
        "text STRING",
        "pii_types ARRAY<STRING>",
        "parser_version STRING",
        "parse_error STRING",
    ]
)

# Current state per object key. Copies of the same content visible to the same groups point at
# one canonical key, so gold indexes each such document once.
SILVER_DOCUMENTS_DDL = (
    f"{PARSED_DDL}, canonical_key STRING, is_duplicate BOOLEAN, updated_at TIMESTAMP"
)

# Output of `ingestion.spark.chunks.chunk_rows`; gold adds `updated_at`.
CHUNKS_DDL = ", ".join(
    [
        "chunk_id STRING",
        "doc_id STRING",
        "key STRING",
        "ordinal INT",
        "heading STRING",
        "content STRING",
        "content_hash STRING",
        "row_hash STRING",
        "title STRING",
        "source_type STRING",
        "source_uri STRING",
        "owner STRING",
        "allowed_groups ARRAY<STRING>",
        "source_updated_at STRING",
        "doc_version STRING",
        "pii_types ARRAY<STRING>",
        "parser_version STRING",
        "chunker_version STRING",
    ]
)

# Change Data Feed on this table is the indexer's input.
GOLD_CHUNKS_DDL = f"{CHUNKS_DDL}, updated_at TIMESTAMP"


def column_names(ddl: str) -> list[str]:
    """Top-level column names of a DDL string (types may contain commas inside <...>)."""
    names, depth, start = [], 0, 0
    for i, char in enumerate(ddl + ","):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "," and depth == 0:
            names.append(ddl[start:i].strip().split(" ", 1)[0])
            start = i + 1
    return names
