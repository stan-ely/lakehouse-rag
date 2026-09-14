"""Assembles and validates the full document corpus."""

from collections import Counter

from data_gen.conversations import build_conversations
from data_gen.models import GROUPS, SourceDocument
from data_gen.static_docs import build_static_documents
from data_gen.world import AS_OF, World


class CorpusError(ValueError):
    pass


def build_corpus(world: World, seed: int) -> list[SourceDocument]:
    documents = [*build_static_documents(world), *build_conversations(world, seed)]
    validate_corpus(documents)
    return documents


def validate_corpus(documents: list[SourceDocument]) -> None:
    problems: list[str] = []
    for field in ("doc_id", "key"):
        dupes = [v for v, n in Counter(getattr(d, field) for d in documents).items() if n > 1]
        if dupes:
            problems.append(f"duplicate {field}: {dupes[:5]}")
    for doc in documents:
        if not doc.allowed_groups or not set(doc.allowed_groups) <= GROUPS:
            problems.append(f"{doc.doc_id}: invalid allowed_groups {doc.allowed_groups}")
        if doc.updated_at > AS_OF:
            problems.append(f"{doc.doc_id}: updated_at is after the dataset as-of date")
        if bool(doc.sections) == (doc.payload is not None):
            problems.append(f"{doc.doc_id}: needs exactly one of sections or payload")
        if not doc.title.isascii():
            problems.append(f"{doc.doc_id}: title must be ASCII to fit S3 user metadata")
    if problems:
        raise CorpusError("; ".join(problems))
