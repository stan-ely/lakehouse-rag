"""Runtime configuration from `RAG_*` environment variables; in AWS, secrets are injected."""

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

    llm_provider: Literal["fake", "bedrock", "anthropic"] = "fake"
    # None picks the provider's default (see app.llm.DEFAULT_MODELS).
    llm_model: str | None = None
    llm_max_tokens: int = Field(default=1024, ge=1)
    llm_timeout_seconds: float = Field(default=30, gt=0)
    anthropic_api_key: SecretStr | None = None
    bedrock_region: str = "us-east-1"

    @property
    def dsn(self) -> str:
        return self.database_url.replace("+psycopg", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
