"""FastAPI application factory.

    mise run api        # uvicorn app.api.main:create_app --factory

Endpoints are synchronous: retrieval uses a psycopg connection pool and CPU-bound ONNX
embeddings, so FastAPI's threadpool is the right execution model and no event loop is blocked.
Dependencies can be injected (`service`, `readiness`) so tests exercise the HTTP contract
without a database or a model.
"""

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Protocol

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.ratelimit import RateLimiter
from app.api.schemas import QueryRequest, QueryResponse, ReadyResponse
from app.auth import AuthError, Principal, decode_token
from app.bootstrap import Readiness, Stack, build_stack
from app.generation.answer import Answer
from app.llm.base import LLMError, LLMTimeout
from app.llm.resilience import CircuitOpen
from app.observability.logging import (
    bind_contextvars,
    clear_contextvars,
    configure_logging,
    get_logger,
)
from app.observability.metrics import RATE_LIMITED, record_query
from app.settings import Settings, get_settings

log = get_logger(__name__)


class QueryService(Protocol):
    def answer(self, question: str, principal: Principal) -> Answer: ...


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    stack: Stack | None = None
    try:
        if app.state.service is None:
            stack = build_stack(app.state.settings)
            app.state.service = stack.service
            app.state.readiness = stack.readiness
        yield
    finally:
        if stack is not None:
            stack.close()


_bearer = HTTPBearer(auto_error=False)


def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    challenge = {"WWW-Authenticate": "Bearer"}
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token", challenge)
    try:
        return decode_token(credentials.credentials, request.app.state.settings)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token", challenge) from exc


def create_app(
    settings: Settings | None = None,
    *,
    service: QueryService | None = None,
    readiness: Readiness | None = None,
) -> FastAPI:
    app = FastAPI(title="lakehouse-rag", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    configure_logging(app.state.settings.env)
    app.state.service = service
    app.state.readiness = readiness or (lambda: {"service": app.state.service is not None})
    rate = app.state.settings.rate_limit_per_minute
    limiter = RateLimiter(rate, app.state.settings.rate_limit_burst) if rate else None

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness: the process is serving requests."""
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics() -> Response:
        """Prometheus scrape target. Keep it off the public listener: it is unauthenticated."""
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/ready", response_model=ReadyResponse)
    def ready() -> JSONResponse:
        """Readiness: dependencies needed to answer queries are available."""
        checks = app.state.readiness()
        body = ReadyResponse(ready=all(checks.values()), checks=checks)
        code = status.HTTP_200_OK if body.ready else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(body.model_dump(), status_code=code)

    @app.post("/query", response_model=QueryResponse)
    def query(
        body: QueryRequest, principal: Annotated[Principal, Depends(get_principal)]
    ) -> QueryResponse:
        request_id = uuid.uuid4().hex
        clear_contextvars()
        bind_contextvars(request_id=request_id, subject=principal.subject)
        if limiter is not None and (wait := limiter.check(principal.subject)):
            RATE_LIMITED.inc()
            log.warning("query.rate_limited", retry_after_s=round(wait, 1))
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "rate limit exceeded",
                {"Retry-After": str(max(1, round(wait)))},
            )
        started = time.perf_counter()
        try:
            answer = app.state.service.answer(body.question, principal)
        except CircuitOpen as exc:
            log.error("query.circuit_open")
            retry_after = str(int(app.state.settings.llm_breaker_reset_seconds))
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "language model unavailable",
                {"Retry-After": retry_after},
            ) from exc
        except LLMTimeout as exc:
            log.error("query.provider_timeout")
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT, "language model timed out"
            ) from exc
        except LLMError as exc:
            log.error("query.provider_failed", error=type(exc).__name__)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "language model unavailable") from exc

        seconds = time.perf_counter() - started
        record_query(answer, seconds)
        log.info(
            "query.served",
            route=answer.route,
            refused=answer.refused,
            refusal_reason=answer.refusal_reason,
            grounded=answer.grounded,
            citations=len(answer.citations),
            flagged_sources=len(answer.flagged_sources),
            model=answer.model,
            cost_usd=None if answer.cost_usd is None else float(answer.cost_usd),
            duration_ms=round(seconds * 1000, 1),
        )
        return QueryResponse.from_answer(
            request_id, answer, expose_sql=app.state.settings.expose_sql
        )

    return app
