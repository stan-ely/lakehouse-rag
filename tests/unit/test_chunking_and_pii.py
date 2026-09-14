import pytest

from ingestion.chunking import chunk_document
from ingestion.parsers import ParsedDocument, Section
from ingestion.pii import detect_pii, mask_pii


def _doc(*sections: Section) -> ParsedDocument:
    return ParsedDocument("Handbook", sections)


def test_small_sections_become_one_chunk_each_with_context_header() -> None:
    chunks = chunk_document(_doc(Section("PTO", "Accrues monthly."), Section("Sick", "Ten days.")))

    assert [(c.ordinal, c.heading) for c in chunks] == [(0, "PTO"), (1, "Sick")]
    assert chunks[0].content("Handbook") == "Handbook > PTO\n\nAccrues monthly."


def test_chunks_respect_target_and_never_cross_sections() -> None:
    paragraphs = "\n".join(f"Paragraph {i} " + "word " * 40 for i in range(12))
    chunks = chunk_document(_doc(Section("A", paragraphs), Section("B", "tail")), target_chars=500)

    assert all(len(c.text) <= 500 + 150 for c in chunks)
    assert {c.heading for c in chunks[:-1]} == {"A"}
    assert chunks[-1].heading == "B"
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_consecutive_chunks_overlap_at_word_boundary() -> None:
    text = "\n".join(f"Sentence number {i} is here." for i in range(60))
    first, second, *_ = chunk_document(_doc(Section("S", text)), target_chars=300, overlap_chars=60)

    assert second.text.split("\n", 1)[0] in first.text
    assert not second.text.startswith(" ")


def test_oversized_paragraph_is_split_on_sentences_then_hard_wrapped() -> None:
    long_sentence = "x" * 700
    text = "Short one. " + long_sentence + ". Another short one."
    chunks = chunk_document(_doc(Section(None, text)), target_chars=300, overlap_chars=0)

    assert "".join(c.text.replace("\n", "") for c in chunks).count("x") == 700
    assert all(len(c.text) <= 300 for c in chunks)


def test_overlap_must_be_smaller_than_target() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_document(_doc(Section(None, "x")), target_chars=100, overlap_chars=100)


def test_content_hash_ignores_title_but_tracks_heading() -> None:
    a, b = chunk_document(_doc(Section("One", "same"), Section("Two", "same")))
    assert a.content_hash != b.content_hash


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Reach me at dana.ruiz@acme-freight.example today", ["email"]),
        ("Call (303) 555-0142 or 303.555.0199 x12", ["phone"]),
        ("SSN 123-45-6789 on file", ["ssn"]),
        ("Card 4111 1111 1111 1111 declined", ["card"]),
        ("Shipment SHP-000123 weighs 1200 kg; invoice INV-2026-0042", []),
        ("Order 1234 5678 9012 3456 is not a valid card", []),
    ],
)
def test_detect_pii(text: str, expected: list[str]) -> None:
    assert detect_pii(text) == expected


def test_mask_pii_replaces_each_match() -> None:
    masked = mask_pii("Email a@b.example or call 303-555-0142.")
    assert masked == "Email [EMAIL] or call [PHONE]."
