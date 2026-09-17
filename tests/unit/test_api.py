from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.main import create_app
from app.auth import Principal, mint_token
from app.generation.answer import Answer, Citation
from app.llm.base import LLMError, LLMTimeout, Usage
from app.settings import Settings

SETTINGS = Settings(jwt_secret=SecretStr("api-test-secret-0123456789abcdef01234"))

GROUNDED = Answer(
    text="Refunds post within 5 days [1].",
    refused=False,
    refusal_reason=None,
    citations=[Citation(1, "doc-1:0000", "doc-1", "Refund Policy", "s3://raw/wiki/r.md", "v3")],
    grounded=True,
    retrieved=[],
    model="claude-haiku-4-5",
    provider="fake",
    usage=Usage(1200, 40),
    cost_usd=Decimal("0.0014"),
    timings_ms={"retrieval": 12.5, "generation": 300.0},
)


class StubService:
    def __init__(self, answer: Answer = GROUNDED, error: Exception | None = None) -> None:
        self.result = answer
        self.error = error
        self.calls: list[tuple[str, Principal]] = []

    def answer(self, question: str, principal: Principal) -> Answer:
        self.calls.append((question, principal))
        if self.error:
            raise self.error
        return self.result


def _client(service: StubService, checks: dict[str, bool] | None = None) -> TestClient:
    ready = checks or {"database": True, "embedder": True}
    return TestClient(create_app(SETTINGS, service=service, readiness=lambda: ready))


def _auth(groups: list[str] | None = None) -> dict[str, str]:
    token = mint_token(SETTINGS, "ana", groups if groups is not None else ["all-staff", "sales"])
    return {"Authorization": f"Bearer {token}"}


def test_health_needs_no_dependencies() -> None:
    response = _client(StubService()).get("/health")
    assert response.json() == {"status": "ok"}


def test_ready_reports_each_check_and_fails_with_503() -> None:
    ok = _client(StubService()).get("/ready")
    down = _client(StubService(), {"database": False, "embedder": True}).get("/ready")

    assert ok.status_code == 200
    assert down.status_code == 503
    assert down.json() == {"ready": False, "checks": {"database": False, "embedder": True}}


def test_query_without_a_token_is_401_with_bearer_challenge() -> None:
    service = StubService()
    response = _client(service).post("/query", json={"question": "Refunds?"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert service.calls == []


def test_query_with_an_invalid_token_is_401() -> None:
    response = _client(StubService()).post(
        "/query", json={"question": "Refunds?"}, headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


@pytest.mark.parametrize("body", [{"question": ""}, {"question": "x", "groups": ["hr"]}, {}])
def test_query_body_is_validated(body: dict[str, object]) -> None:
    # Groups can never be supplied by the client: extra fields are rejected outright.
    assert _client(StubService()).post("/query", json=body, headers=_auth()).status_code == 422


def test_query_returns_the_grounded_answer_for_the_token_identity() -> None:
    service = StubService()

    response = _client(service).post("/query", json={"question": "Refunds?"}, headers=_auth())

    body = response.json()
    assert response.status_code == 200
    assert body["answer"] == "Refunds post within 5 days [1]."
    assert body["citations"][0]["source_uri"] == "s3://raw/wiki/r.md"
    assert body["usage"]["input_tokens"] == 1200
    assert body["cost_usd"] == pytest.approx(0.0014)
    assert len(body["request_id"]) == 32
    assert service.calls == [("Refunds?", Principal("ana", frozenset({"all-staff", "sales"})))]


@pytest.mark.parametrize(("error", "code"), [(LLMTimeout("slow"), 504), (LLMError("down"), 502)])
def test_provider_failures_map_to_gateway_errors(error: Exception, code: int) -> None:
    response = _client(StubService(error=error)).post(
        "/query", json={"question": "Refunds?"}, headers=_auth()
    )
    assert response.status_code == code
    assert "slow" not in response.text
    assert "down" not in response.text


SQL_ANSWER = Answer(
    text="Four shipments arrived late [1].",
    refused=False,
    refusal_reason=None,
    citations=[],
    grounded=True,
    retrieved=[],
    route="sql",
    sql="SELECT count(*) FROM rag_ops.shipments WHERE late",
    sql_error=None,
)


def test_generated_sql_is_withheld_unless_the_setting_asks_for_it() -> None:
    hidden = _client(StubService(SQL_ANSWER)).post(
        "/query", json={"question": "Late shipments?"}, headers=_auth()
    )
    assert hidden.json()["sql"] is None
    assert "rag_ops.shipments" not in hidden.text

    debug = TestClient(
        create_app(
            SETTINGS.model_copy(update={"expose_sql": True}),
            service=StubService(SQL_ANSWER),
            readiness=lambda: {"database": True},
        )
    ).post("/query", json={"question": "Late shipments?"}, headers=_auth())
    assert debug.json()["sql"] == "SELECT count(*) FROM rag_ops.shipments WHERE late"
