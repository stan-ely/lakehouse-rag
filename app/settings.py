"""Runtime configuration from `RAG_*` environment variables; in AWS, secrets are injected."""

from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAG_", extra="ignore")

    env: Literal["local", "ci", "aws"] = "local"
    database_url: str = "postgresql://rag:rag@localhost:5432/rag"
    db_pool_min: int = 1
    db_pool_max: int = 4

    # HS256 keeps local and CI self-contained; the AWS environment injects the secret.
    jwt_secret: SecretStr = SecretStr("local-dev-secret-change-me-0123456789")
    jwt_issuer: str = "lakehouse-rag"
    jwt_audience: str = "lakehouse-rag-api"
    jwt_leeway_seconds: int = 30

    embed_model: str = "BAAI/bge-small-en-v1.5"
    retrieval_k: int = Field(default=8, ge=1, le=50)
    retrieval_candidates: int = Field(default=40, ge=1, le=500)
    rrf_k: int = Field(default=60, ge=1)
    # Below this cosine similarity, and with no full-text match, the service refuses to answer.
    min_similarity: float = Field(default=0.55, ge=0, le=1)

    # Text-to-SQL runs as the least-privileged `rag_sql` login (migration 0006), never as `rag`.
    sql_database_url: str = "postgresql://rag_sql:rag_sql@localhost:5432/rag"
    sql_pool_max: int = 2
    sql_max_rows: int = Field(default=200, ge=1, le=5000)
    sql_statement_timeout_ms: int = Field(default=5000, ge=100)
    # "Today" for relative dates in SQL ("last month"). The synthetic dataset is frozen at
    # 2026-08-31, so local environments pin it; unset means the real current date.
    as_of_date: date | None = None

    # Generated SQL names tables, columns and filters, so returning it discloses the schema to
    # anyone who can read a response body. Off by default; the demo UI turns it on.
    expose_sql: bool = False

    llm_provider: Literal["fake", "bedrock", "anthropic"] = "fake"
    # None picks the provider's default (see app.llm.DEFAULT_MODELS).
    llm_model: str | None = None
    llm_max_tokens: int = Field(default=1024, ge=1)
    llm_timeout_seconds: float = Field(default=30, gt=0)
    # Per-caller /query limit, counted in this process only (see app/api/ratelimit.py).
    # 0 disables it.
    rate_limit_per_minute: float = Field(default=30, ge=0)
    rate_limit_burst: int = Field(default=10, ge=1)

    # Consecutive provider failures that open the circuit; 0 disables it.
    llm_breaker_failures: int = Field(default=5, ge=0)
    llm_breaker_reset_seconds: float = Field(default=30, gt=0)
    anthropic_api_key: SecretStr | None = None
    bedrock_region: str = "us-east-1"
    # Empty means real Bedrock. Local shells point every AWS SDK at Floci, whose Bedrock stub
    # returns canned text, so reaching the real service has to be asked for explicitly.
    bedrock_endpoint_url: str = ""

    @property
    def bedrock_endpoint(self) -> str | None:
        return self.bedrock_endpoint_url or None

    @property
    def dsn(self) -> str:
        return self.database_url.replace("+psycopg", "")

    @property
    def sql_dsn(self) -> str:
        return self.sql_database_url.replace("+psycopg", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
