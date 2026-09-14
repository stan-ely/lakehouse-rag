"""Source document model shared by the corpus builders and the renderers."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

GROUPS = frozenset({"all-staff", "exec", "sales", "support", "ops", "finance", "hr", "engineering"})

_SOURCE_TYPES = {"tickets": "ticket", "chat": "chat", "email": "email", "wiki": "wiki"}


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


type Block = str | Table


@dataclass(frozen=True)
class Section:
    heading: str
    blocks: tuple[Block, ...]


def section(heading: str, *blocks: Block) -> Section:
    return Section(heading, blocks)


@dataclass(frozen=True)
class SourceDocument:
    """One object in the raw bucket.

    Rich documents (pdf, docx, wiki) carry `sections`; conversations (tickets, chat, email)
    carry a JSON `payload`. ACLs travel as S3 object metadata, never inside the content.
    """

    doc_id: str
    key: str
    title: str
    allowed_groups: tuple[str, ...]
    owner: str
    updated_at: datetime
    sections: tuple[Section, ...] = ()
    payload: dict[str, Any] | None = None

    @property
    def source_type(self) -> str:
        prefix = self.key.split("/", 1)[0]
        return _SOURCE_TYPES.get(prefix) or self.key.rsplit(".", 1)[-1]

    @property
    def classification(self) -> str:
        if "all-staff" in self.allowed_groups:
            return "Internal"
        if set(self.allowed_groups) <= {"hr", "exec"}:
            return "Restricted"
        return "Confidential"
