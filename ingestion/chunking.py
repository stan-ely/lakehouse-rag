"""Heading-aware chunking.

Chunks never cross a section boundary, so every chunk can cite one heading. Within a section,
paragraphs are packed greedily up to `target_chars`; an oversized paragraph is split on
sentences, then hard-wrapped. Consecutive chunks of the same section overlap by up to
`overlap_chars` of trailing text so a fact straddling a boundary is retrievable from either.

Sizes are in characters rather than tokens to keep this dependency-free: ~1,200 characters is
roughly 300 tokens, well inside bge-small's 512-token window.
"""

import hashlib
import re
from dataclasses import dataclass

from ingestion.parsers import ParsedDocument

CHUNKER_VERSION = "1"
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    heading: str | None
    text: str

    def content(self, title: str | None) -> str:
        """Text as indexed: a contextual header so retrieval sees what the chunk is about."""
        header = " > ".join(p for p in (title, self.heading) if p)
        return f"{header}\n\n{self.text}" if header else self.text

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.heading}\n{self.text}".encode()).hexdigest()


def _pieces(paragraph: str, limit: int) -> list[str]:
    if len(paragraph) <= limit:
        return [paragraph]
    out: list[str] = []
    buffer = ""
    for sentence in _SENTENCE.split(paragraph):
        while len(sentence) > limit:
            if buffer:
                out.append(buffer)
                buffer = ""
            out.append(sentence[:limit])
            sentence = sentence[limit:]
        if buffer and len(buffer) + 1 + len(sentence) > limit:
            out.append(buffer)
            buffer = sentence
        else:
            buffer = f"{buffer} {sentence}" if buffer else sentence
    if buffer:
        out.append(buffer)
    return out


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return ""
    cut = text[-overlap:]
    # Start the overlap at a word boundary so it never begins mid-word.
    space = cut.find(" ")
    return cut[space + 1 :] if 0 <= space < len(cut) - 1 else cut


def chunk_document(
    doc: ParsedDocument, *, target_chars: int = 1200, overlap_chars: int = 150
) -> list[Chunk]:
    if overlap_chars >= target_chars:
        raise ValueError("overlap_chars must be smaller than target_chars")
    chunks: list[Chunk] = []
    for section in doc.sections:
        paragraphs = [p.strip() for p in section.text.split("\n") if p.strip()]
        pieces = [piece for p in paragraphs for piece in _pieces(p, target_chars)]
        buffer = ""
        for piece in pieces:
            if buffer and len(buffer) + 1 + len(piece) > target_chars:
                chunks.append(Chunk(len(chunks), section.heading, buffer))
                carry = _tail(buffer, overlap_chars)
                buffer = f"{carry}\n{piece}" if carry else piece
            else:
                buffer = f"{buffer}\n{piece}" if buffer else piece
        if buffer:
            chunks.append(Chunk(len(chunks), section.heading, buffer))
        elif section.heading and not pieces:
            chunks.append(Chunk(len(chunks), section.heading, section.heading))
    return chunks
