"""Turns raw source bytes into headed text sections.

Parsers are plain Python with no Spark dependency, so they run unchanged inside a Spark
`mapInPandas`, on Databricks serverless and in unit tests. Each parser keeps the document's
own structure (headings, tables, message boundaries) because chunk boundaries and citations
are only as good as the sections they come from, and strips presentation chrome (wiki
navigation, footers, markdown front matter) that would otherwise pollute every chunk.
"""

import hashlib
import io
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup, Tag
from docx import Document
from pypdf import PdfReader

PARSER_VERSION = "1"


class ParseError(ValueError):
    """The object could not be parsed; silver records it instead of failing the batch."""


@dataclass(frozen=True)
class Section:
    heading: str | None
    text: str


@dataclass(frozen=True)
class ParsedDocument:
    title: str | None
    sections: tuple[Section, ...]

    @property
    def text(self) -> str:
        parts = []
        for sec in self.sections:
            parts += [sec.heading, sec.text] if sec.heading else [sec.text]
        return "\n\n".join(p for p in parts if p)

    @property
    def fingerprint(self) -> str:
        """Hash of the normalized body, ignoring title and preamble.

        Two exports of the same page differ in title ("Copy of ...") and last-updated line
        but share every headed section, so they fingerprint identically and silver can
        collapse them. Documents without headings fall back to their whole text.
        """
        headed = [s for s in self.sections if s.heading] or list(self.sections)
        normalized = "\n".join(_normalize(f"{s.heading or ''}\n{s.text}") for s in headed)
        return hashlib.sha256(normalized.encode()).hexdigest()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _build(title: str | None, blocks: Iterator[tuple[str, str]]) -> ParsedDocument:
    """Folds a stream of ("heading" | "text", value) blocks into sections."""
    sections: list[Section] = []
    heading: str | None = None
    lines: list[str] = []

    def flush() -> None:
        if heading or lines:
            sections.append(Section(heading, "\n".join(lines).strip()))

    for kind, value in blocks:
        value = value.strip()
        if not value:
            continue
        if kind == "heading":
            flush()
            heading, lines = value, []
        else:
            lines.append(value)
    flush()
    sections = [s for s in sections if s.text or s.heading]
    if not sections:
        raise ParseError("document has no extractable text")
    return ParsedDocument(title, tuple(sections))


def parse_pdf(data: bytes) -> ParsedDocument:
    """Uses font size to recover headings, which plain text extraction flattens."""
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ParseError(f"unreadable pdf: {exc}") from exc

    lines: list[tuple[str, float]] = []
    current: list[str] = []
    size = 0.0

    def visit(text: str, _cm: Any, tm: Any, _font: Any, font_size: float) -> None:
        nonlocal size
        effective = abs(float(font_size) * float(tm[0])) if tm else float(font_size)
        for i, piece in enumerate(text.split("\n")):
            if i:
                lines.append(("".join(current), size))
                current.clear()
                size = 0.0
            if piece.strip():
                current.append(piece)
                size = max(size, effective)

    for page in reader.pages:
        page.extract_text(visitor_text=visit)
        lines.append(("".join(current), size))
        current.clear()
        size = 0.0

    title = next((t.strip() for t, s in lines if s >= 17 and t.strip()), None)

    def blocks() -> Iterator[tuple[str, str]]:
        for text, line_size in lines:
            if line_size >= 17:
                continue
            yield ("heading" if line_size >= 13 else "text"), text

    fallback = reader.metadata.title if reader.metadata else None
    return _build(title or fallback, blocks())


def parse_docx(data: bytes) -> ParsedDocument:
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise ParseError(f"unreadable docx: {exc}") from exc

    title = document.core_properties.title or None

    def blocks() -> Iterator[tuple[str, str]]:
        # Walk the body in order so tables stay next to the paragraphs that introduce them.
        for element in document.element.body.iterchildren():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "p":
                style = element.style or ""
                text = "".join(
                    node.text or "" for node in element.iter() if node.tag.endswith("}t")
                )
                if style == "Title":
                    continue
                yield ("heading" if style.startswith("Heading") else "text"), text
            elif tag == "tbl":
                for row in element.iter():
                    if row.tag.endswith("}tr"):
                        cells = [
                            "".join(t.text or "" for t in cell.iter() if t.tag.endswith("}t"))
                            for cell in row.iter()
                            if cell.tag.endswith("}tc")
                        ]
                        yield "text", " | ".join(cells)

    return _build(title, blocks())


_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def parse_markdown(data: bytes) -> ParsedDocument:
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    title = None
    if match := _FRONT_MATTER.match(text):
        for line in match.group(1).splitlines():
            if line.startswith("title:"):
                raw = line.split(":", 1)[1].strip()
                title = json.loads(raw) if raw.startswith('"') else raw
        text = text[match.end() :]

    def blocks() -> Iterator[tuple[str, str]]:
        nonlocal title
        for line in text.split("\n"):
            if line.startswith("# "):
                title = title or line[2:].strip()
            elif re.match(r"#{2,6} ", line):
                yield "heading", line.lstrip("#")
            elif re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", line.strip()):
                continue  # table separator row
            elif line.startswith("|") and line.rstrip().endswith("|"):
                # Same "a | b" row shape as the docx and html parsers, so fingerprints agree.
                cells = re.split(r"(?<!\\)\|", line.strip()[1:-1])
                yield "text", " | ".join(c.strip().replace("\\|", "|") for c in cells)
            else:
                yield "text", line

    return _build(title, blocks())


_CHROME_SELECTORS = ("#header", "#footer", "nav", "script", "style", ".page-metadata")


def parse_html(data: bytes) -> ParsedDocument:
    soup = BeautifulSoup(data, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else None
    for selector in _CHROME_SELECTORS:
        for node in soup.select(selector):
            if selector == ".page-metadata":
                # Keep the last-updated line as text: it is how readers spot stale pages.
                node.name = "p"
                node.attrs = {}
                continue
            node.decompose()
    root = soup.select_one("#main-content") or soup.body or soup

    def blocks() -> Iterator[tuple[str, str]]:
        for node in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "tr"]):
            assert isinstance(node, Tag)
            if node.name == "h1":
                continue
            if node.name == "tr":
                cells = [c.get_text(" ", strip=True) for c in node.find_all(["th", "td"])]
                yield "text", " | ".join(cells)
            elif node.name.startswith("h"):
                yield "heading", node.get_text(" ", strip=True)
            else:
                yield "text", node.get_text(" ", strip=True)

    return _build(title, blocks())


def _person(value: Any) -> str:
    if isinstance(value, dict):
        name, email = value.get("name"), value.get("email")
        return f"{name} <{email}>" if name and email else str(name or email or "")
    return str(value)


def parse_conversation(data: bytes) -> ParsedDocument:
    """Tickets, chat threads and email threads share one shape: a header plus messages."""
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid json: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise ParseError("conversation payload has no messages list")

    def blocks() -> Iterator[tuple[str, str]]:
        if "ticket_id" in payload:
            customer = payload.get("customer") or {}
            yield "heading", "Ticket details"
            yield "text", f"Ticket: {payload['ticket_id']}"
            yield "text", f"Status: {payload.get('status')} | Priority: {payload.get('priority')}"
            yield (
                "text",
                f"Customer: {customer.get('name')} "
                f"({customer.get('customer_id')}, {customer.get('tier')} tier)",
            )
            yield "text", f"Shipment: {payload.get('shipment_id')}"
            yield "text", f"Requester: {_person(payload.get('requester'))}"
            yield "text", f"Assignee: {_person(payload.get('assignee'))}"
        elif "channel" in payload:
            yield "heading", f"Thread in {payload['channel']}"
        else:
            yield "heading", "Email thread"
            yield "text", f"Subject: {payload.get('subject')}"
        yield "heading", "Messages"
        for message in payload["messages"]:
            if "text" in message:  # chat
                yield "text", f"[{message.get('ts')}] {message.get('user')}: {message['text']}"
            elif "from" in message:  # email
                to = ", ".join(_person(p) for p in message.get("to", []))
                yield "text", f"[{message.get('sent_at')}] From {_person(message['from'])} to {to}:"
                yield "text", str(message.get("body", ""))
            else:  # ticket
                who = f"{message.get('author')} ({message.get('role')})"
                yield "text", f"[{message.get('sent_at')}] {who}: {message.get('body', '')}"

    title = payload.get("subject") or payload.get("channel")
    return _build(str(title) if title else None, blocks())


_PARSERS: dict[str, Callable[[bytes], ParsedDocument]] = {
    "pdf": parse_pdf,
    "docx": parse_docx,
    "md": parse_markdown,
    "html": parse_html,
    "json": parse_conversation,
}


def parse(key: str, data: bytes) -> ParsedDocument:
    extension = key.rsplit(".", 1)[-1].lower()
    parser = _PARSERS.get(extension)
    if parser is None:
        raise ParseError(f"no parser for .{extension}")
    return parser(data)
