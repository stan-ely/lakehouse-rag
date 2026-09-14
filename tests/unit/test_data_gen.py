import io
import json
import zipfile

import pytest

from data_gen.conversations import INJECTED_TICKET_ID, INJECTION_TEXT
from data_gen.corpus import CorpusError, build_corpus, validate_corpus
from data_gen.facts import PAY_BANDS, TIERS
from data_gen.models import SourceDocument
from data_gen.render import render
from data_gen.world import AS_OF, World, build_world

SEED = 7


@pytest.fixture(scope="module")
def world() -> World:
    return build_world(SEED)


@pytest.fixture(scope="module")
def corpus(world: World) -> list[SourceDocument]:
    return build_corpus(world, SEED)


def test_world_is_reproducible_from_seed(world: World) -> None:
    assert build_world(SEED) == world
    assert build_world(SEED + 1) != world


def test_org_chart_is_consistent(world: World) -> None:
    ids = [e.employee_id for e in world.employees]
    assert len(set(ids)) == len(ids)
    assert len({e.email for e in world.employees}) == len(ids)
    seen: set[str] = set()
    for e in world.employees:
        # Managers are emitted before their reports so COPY satisfies the self-referencing FK.
        assert e.manager_id is None or e.manager_id in seen
        seen.add(e.employee_id)
    hubs = {e.location for e in world.employees if e.title == "Hub Supervisor"}
    assert len(hubs) == 8


def test_compensation_falls_within_pay_bands(world: World) -> None:
    assert len(world.compensation) == len(world.employees)
    for comp in world.compensation:
        low, high, bonus = PAY_BANDS[comp.pay_band]
        assert low <= comp.base_salary_usd <= high
        assert comp.bonus_pct == bonus


def test_customers_match_tier_terms(world: World) -> None:
    tiers = {c.tier for c in world.customers}
    assert tiers == set(TIERS)
    for c in world.customers:
        assert c.sla_hours == TIERS[c.tier].first_response_hours
        assert world.employees_by_id[c.account_manager_id].department == "Sales"


def test_shipments_and_invoices_are_consistent(world: World) -> None:
    assert {s.customer_id for s in world.shipments} <= set(world.customers_by_id)
    for s in world.shipments:
        assert s.promised_at > s.booked_at
        assert (s.delivered_at is not None) == (s.status == "delivered")
        assert s.delivered_at is None or s.delivered_at <= AS_OF
    shipments = {s.shipment_id: s for s in world.shipments}
    for inv in world.invoices:
        s = shipments[inv.shipment_id]
        assert s.status == "delivered"
        assert inv.customer_id == s.customer_id
        assert inv.due_on >= inv.issued_on
        assert (inv.paid_on is not None) == (inv.status == "paid")
    statuses = {inv.status for inv in world.invoices}
    assert {"paid", "open", "overdue", "disputed"} <= statuses
    assert any(s.is_late for s in world.shipments)


def test_corpus_covers_every_source_type(corpus: list[SourceDocument]) -> None:
    types = {doc.source_type for doc in corpus}
    assert types == {"pdf", "docx", "wiki", "ticket", "chat", "email"}


def test_corpus_includes_restricted_and_conflicting_documents(
    corpus: list[SourceDocument],
) -> None:
    by_id = {doc.doc_id: doc for doc in corpus}
    assert "all-staff" not in by_id["pol-compensation-2026"].allowed_groups
    assert by_id["exec-board-update-2026-q2"].allowed_groups == ("exec",)
    assert by_id["wiki-legacy-finance-expense-reimbursement"].updated_at.year == 2023
    assert (
        by_id["wiki-support-escalation-matrix"].sections
        == by_id["wiki-support-escalation-matrix-copy"].sections
    )


def test_conversations_reference_real_entities(world: World, corpus: list[SourceDocument]) -> None:
    shipment_ids = {s.shipment_id for s in world.shipments}
    tickets = [doc for doc in corpus if doc.source_type == "ticket"]
    assert tickets
    for doc in tickets:
        assert doc.payload is not None
        assert doc.payload["shipment_id"] in shipment_ids
        assert doc.payload["customer"]["customer_id"] in world.customers_by_id
    injected = next(
        d for d in tickets if d.payload and d.payload["ticket_id"] == INJECTED_TICKET_ID
    )
    assert injected.payload is not None
    assert INJECTION_TEXT in injected.payload["messages"][0]["body"]


def test_corpus_is_reproducible(world: World, corpus: list[SourceDocument]) -> None:
    assert build_corpus(world, SEED) == corpus


def test_validation_rejects_unknown_groups(corpus: list[SourceDocument]) -> None:
    doc = corpus[0]
    bad = SourceDocument(
        doc_id="bad",
        key="wiki/bad.md",
        title="Bad",
        allowed_groups=("everyone",),
        owner=doc.owner,
        updated_at=doc.updated_at,
        sections=doc.sections,
    )
    with pytest.raises(CorpusError, match="invalid allowed_groups"):
        validate_corpus([bad])


@pytest.mark.parametrize("suffix", ["pdf", "docx", "md", "html", "json"])
def test_rendering_is_byte_reproducible(corpus: list[SourceDocument], suffix: str) -> None:
    doc = next(d for d in corpus if d.key.endswith(f".{suffix}"))
    first, second = render(doc), render(doc)
    assert first == second
    if suffix == "pdf":
        assert first.startswith(b"%PDF-")
    elif suffix == "docx":
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            assert doc.title in archive.read("word/document.xml").decode()
    elif suffix == "json":
        assert json.loads(first) == doc.payload
    else:
        assert doc.title in first.decode()
