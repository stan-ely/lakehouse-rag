"""Query path against the real index: hybrid retrieval, ACLs, the HTTP API and Bedrock on Floci.

Expects gold chunks in the lake (`mise run ingest`, or the ingest tests that run first).
"""

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from fastapi.testclient import TestClient
from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg_pool import ConnectionPool

from app.api.main import create_app
from app.auth import mint_token
from app.llm.bedrock import BedrockProvider
from app.llm.cost import cost_usd
from app.retrieval.embedder import FastQueryEmbedder
from app.retrieval.hybrid import HybridRetriever
from app.settings import Settings
from ingestion.indexer.embed import FastEmbedder
from ingestion.indexer.run import index
from ingestion.indexer.source import GoldSource
from ingestion.indexer.store import PostgresStore
from tests.integration.lake import ENDPOINT, LAKE_BUCKET, STORAGE_OPTIONS

pytestmark = pytest.mark.integration

SETTINGS = Settings(llm_provider="fake")


@pytest.fixture(scope="module")
def pool() -> Iterator[ConnectionPool[Connection[Any]]]:
    with ConnectionPool(
        SETTINGS.dsn, min_size=1, max_size=2, kwargs={"autocommit": True}, configure=register_vector
    ) as connection_pool:
        with connection_pool.connection() as conn:
            # Catch the index up with gold (a no-op when current), whatever ran before.
            source = GoldSource(f"s3://{LAKE_BUCKET}/delta/gold/chunks", STORAGE_OPTIONS)
            index(source, PostgresStore(conn), FastEmbedder())
            row = conn.execute("SELECT count(*) FROM rag.chunks").fetchone()
        assert row is not None
        assert row[0] > 0, "index is empty: run `mise run index` first"
        yield connection_pool


@pytest.fixture(scope="module")
def retriever(pool: ConnectionPool[Connection[Any]]) -> HybridRetriever:
    return HybridRetriever(pool, FastQueryEmbedder(SETTINGS.embed_model))


def test_every_result_is_visible_to_the_caller(retriever: HybridRetriever) -> None:
    groups = {"all-staff", "sales"}

    results = retriever.search("salary bands and compensation review", sorted(groups), 20)

    assert results
    assert all(groups & set(r.allowed_groups) for r in results)


def test_hr_only_documents_reach_hr_but_never_sales(
    pool: ConnectionPool[Connection[Any]], retriever: HybridRetriever
) -> None:
    with pool.connection() as conn:  # table owner: sees every row
        rows = conn.execute(
            """
            SELECT doc_id, title FROM rag.documents
            WHERE 'hr' = ANY(allowed_groups) AND allowed_groups <@ ARRAY['hr', 'exec']::text[]
            ORDER BY doc_id
            """
        ).fetchall()
    restricted = {doc_id for doc_id, _ in rows}
    assert restricted, "seed data should include HR/exec-only documents"
    title = rows[0][1]

    as_hr = retriever.search(title, ["all-staff", "hr"], 10)
    as_sales = retriever.search(title, ["all-staff", "sales"], 10)

    assert any(r.doc_id in restricted for r in as_hr)
    assert not any(r.doc_id in restricted for r in as_sales)


def test_exact_identifiers_are_found_through_full_text(retriever: HybridRetriever) -> None:
    results = retriever.search("TCK-00013", ["support"], 5)

    assert results[0].doc_id == "ticket-TCK-00013"
    assert results[0].lexical_rank == 1


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app(SETTINGS)) as test_client:
        yield test_client


def _auth(groups: list[str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(SETTINGS, 'it-user', groups)}"}


def test_ready_checks_database_and_model(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": True, "sql_database": True, "embedder": True}


def test_query_returns_a_cited_answer(client: TestClient) -> None:
    response = client.post(
        "/query",
        json={"question": "How are support tickets escalated?"},
        headers=_auth(["all-staff", "support"]),
    )

    body = response.json()
    assert response.status_code == 200
    assert not body["refused"], body
    assert body["citations"]
    assert all(c["source_uri"].startswith("s3://") for c in body["citations"])


def test_caller_without_groups_is_refused(client: TestClient) -> None:
    response = client.post(
        "/query", json={"question": "What are the salary bands?"}, headers=_auth([])
    )

    body = response.json()
    assert body["refused"]
    assert body["refusal_reason"] == "no_accessible_sources"
    assert body["citations"] == []


def test_bedrock_provider_round_trips_through_floci() -> None:
    client = boto3.client("bedrock-runtime", endpoint_url=ENDPOINT, region_name="us-east-1")
    provider = BedrockProvider("us.anthropic.claude-haiku-4-5-20251001-v1:0", client=client)

    completion = provider.complete(system="Be brief.", prompt="Say hello.", max_tokens=16)

    assert completion.text
    assert completion.usage.input_tokens > 0
    cost = cost_usd(completion.model, completion.usage)
    assert cost is not None
    assert cost > 0
