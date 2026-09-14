"""Parsers against the generator's real renderings, so format drift breaks a test."""

from datetime import UTC, datetime

import pytest

from data_gen.corpus import build_corpus
from data_gen.models import SourceDocument, section
from data_gen.models import Table as DocTable
from data_gen.render import render
from data_gen.world import build_world
from ingestion.parsers import ParseError, parse, parse_conversation

SEED = 7


@pytest.fixture(scope="module")
def corpus() -> dict[str, SourceDocument]:
    world = build_world(SEED, customer_count=20, shipment_count=600)
    return {doc.doc_id: doc for doc in build_corpus(world, SEED)}


def _sample(ext: str) -> SourceDocument:
    return SourceDocument(
        doc_id="t",
        key=f"wiki/t.{ext}" if ext in {"md", "html"} else f"documents/t.{ext}",
        title="Parcel Handling Standard",
        allowed_groups=("ops",),
        owner="Operations",
        updated_at=datetime(2026, 5, 1, tzinfo=UTC),
        sections=(
            section("Scope", "Applies to every hub.", "Drivers must scan at pickup."),
            section(
                "Weight limits",
                "Heavier parcels need two people.",
                DocTable(("Mode", "Max kg"), (("road", "32"), ("air", "20"))),
            ),
        ),
    )


@pytest.mark.parametrize("ext", ["pdf", "docx", "md", "html"])
def test_rich_formats_recover_title_headings_and_tables(ext: str) -> None:
    doc = _sample(ext)
    parsed = parse(doc.key, render(doc))

    assert parsed.title == "Parcel Handling Standard"
    headings = [s.heading for s in parsed.sections if s.heading]
    assert headings == ["Scope", "Weight limits"]
    limits = next(s for s in parsed.sections if s.heading == "Weight limits")
    assert "two people" in limits.text
    assert "air" in limits.text
    assert "20" in limits.text


# PDF is excluded: text extraction loses table cell separators, so its fingerprint differs.
@pytest.mark.parametrize("ext", ["docx", "html"])
def test_structured_formats_of_the_same_content_share_a_fingerprint(ext: str) -> None:
    reference = parse("wiki/t.md", render(_sample("md"))).fingerprint
    assert parse(_sample(ext).key, render(_sample(ext))).fingerprint == reference


def test_html_strips_wiki_chrome_but_keeps_last_updated(corpus: dict[str, SourceDocument]) -> None:
    doc = next(d for d in corpus.values() if d.key.endswith(".html"))
    parsed = parse(doc.key, render(doc))

    assert "Powered by Confluence" not in parsed.text
    assert "Spaces" not in parsed.text
    assert "Last updated" in parsed.text


def test_markdown_front_matter_is_not_indexed(corpus: dict[str, SourceDocument]) -> None:
    doc = corpus["wiki-support-escalation-matrix"]
    parsed = parse(doc.key, render(doc))

    assert parsed.title == doc.title
    assert "owner:" not in parsed.text


def test_copied_wiki_page_is_a_duplicate_by_fingerprint(corpus: dict[str, SourceDocument]) -> None:
    original = corpus["wiki-support-escalation-matrix"]
    copy = corpus["wiki-support-escalation-matrix-copy"]

    assert render(original) != render(copy)
    assert parse(original.key, render(original)).fingerprint == (
        parse(copy.key, render(copy)).fingerprint
    )


@pytest.mark.parametrize("prefix", ["tickets/", "chat/", "email/"])
def test_conversations_keep_every_message(corpus: dict[str, SourceDocument], prefix: str) -> None:
    doc = next(d for d in corpus.values() if d.key.startswith(prefix))
    assert doc.payload is not None
    parsed = parse(doc.key, render(doc))

    messages = next(s for s in parsed.sections if s.heading == "Messages")
    assert len(messages.text.splitlines()) >= len(doc.payload["messages"])


def test_ticket_header_names_customer_and_shipment(corpus: dict[str, SourceDocument]) -> None:
    doc = next(d for d in corpus.values() if d.key.startswith("tickets/"))
    assert doc.payload is not None
    parsed = parse(doc.key, render(doc))

    details = next(s for s in parsed.sections if s.heading == "Ticket details")
    assert doc.payload["customer"]["name"] in details.text
    assert doc.payload["shipment_id"] in details.text


def test_every_generated_document_parses(corpus: dict[str, SourceDocument]) -> None:
    for doc in corpus.values():
        parsed = parse(doc.key, render(doc))
        assert parsed.text.strip(), doc.key


@pytest.mark.parametrize(
    ("key", "data"),
    [
        ("documents/bad.pdf", b"not a pdf"),
        ("documents/bad.docx", b"not a zip"),
        ("tickets/bad.json", b"{"),
        ("tickets/empty.json", b'{"messages": "nope"}'),
        ("wiki/empty.md", b"---\ntitle: x\n---\n"),
        ("smoke/file.txt", b"hello"),
    ],
)
def test_bad_input_raises_parse_error(key: str, data: bytes) -> None:
    with pytest.raises(ParseError):
        parse(key, data)


def test_chat_thread_lines_carry_author() -> None:
    parsed = parse_conversation(
        b'{"channel": "#ops", "messages": [{"user": "Ana", "ts": "t1", "text": "Truck late"}]}'
    )
    assert parsed.sections[-1].text == "[t1] Ana: Truck late"
