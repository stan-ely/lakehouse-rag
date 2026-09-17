"""Builds the query stack from settings: pools, embedder, provider, retrieval, SQL and routing.

The API's lifespan and the evaluation harness need exactly the same wiring, so it lives here
once. Retrieval and the SQL tool use different logins on purpose: the SQL tool never holds the
privileges the retriever has (see docs/adr/0002).
"""

from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any

from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from app.generation.answer import AnswerService
from app.llm import build_provider
from app.orchestrator import QueryService
from app.retrieval.embedder import FastQueryEmbedder
from app.retrieval.hybrid import HybridRetriever
from app.router.classifier import Router
from app.settings import Settings
from app.sql_tool.executor import SqlExecutor
from app.sql_tool.service import SqlTool

Readiness = Callable[[], dict[str, bool]]


def _ping(pool: ConnectionPool[Any]) -> bool:
    try:
        with pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")
    except Exception:
        return False
    return True


def open_pool(dsn: str, min_size: int, max_size: int, **options: Any) -> ConnectionPool[Any]:
    pool: ConnectionPool[Any] = ConnectionPool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        kwargs={"autocommit": True},
        open=False,
        **options,
    )
    pool.open(wait=True, timeout=10)
    return pool


@dataclass
class Stack:
    service: QueryService
    readiness: Readiness
    pools: list[ConnectionPool[Any]]
    # Exposed so the evaluation harness can measure retrieval on its own, without a model.
    retriever: HybridRetriever | None = None

    def close(self) -> None:
        for pool in self.pools:
            pool.close()

    def __enter__(self) -> "Stack":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def build_stack(settings: Settings) -> Stack:
    pool = open_pool(
        settings.dsn, settings.db_pool_min, settings.db_pool_max, configure=register_vector
    )
    pools = [pool]
    try:
        sql_pool = open_pool(settings.sql_dsn, 1, settings.sql_pool_max)
        pools.append(sql_pool)
        embedder = FastQueryEmbedder(settings.embed_model)
        embedder.load()
        llm = build_provider(settings)
        retriever = HybridRetriever(
            pool, embedder, candidates=settings.retrieval_candidates, rrf_k=settings.rrf_k
        )
        answers = AnswerService(
            retriever,
            llm,
            k=settings.retrieval_k,
            min_similarity=settings.min_similarity,
            max_tokens=settings.llm_max_tokens,
        )
        sql_tool = SqlTool(
            llm,
            SqlExecutor(sql_pool, statement_timeout_ms=settings.sql_statement_timeout_ms),
            max_rows=settings.sql_max_rows,
            as_of=settings.as_of_date,
        )
    except Exception:
        for opened in pools:
            opened.close()
        raise

    def readiness() -> dict[str, bool]:
        return {
            "database": _ping(pool),
            "sql_database": _ping(sql_pool),
            "embedder": embedder.loaded,
        }

    return Stack(QueryService(Router(llm), answers, sql_tool), readiness, pools, retriever)
