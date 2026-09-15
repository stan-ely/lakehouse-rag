"""Retrieve, decide whether there is enough evidence, generate, and verify citations.

Refusals use one user-facing message whatever the reason, so a caller cannot tell "nothing
exists" from "something exists that you may not see". The machine-readable reason only says
which of *this caller's* checks failed, which reveals nothing about other users' documents.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Protocol

from app.auth import Principal
from app.generation.prompt import (
    REFUSAL_SENTINEL,
    SYSTEM_PROMPT,
    build_prompt,
    citation_indexes,
    strip_citations,
)
from app.llm.base import LLMProvider, Usage
from app.llm.cost import cost_usd
from app.retrieval.hybrid import RetrievedChunk
from ingestion.pii import mask_pii

REFUSAL_MESSAGE = (
    "I couldn't find enough evidence in the documents available to you to answer that."
)


class Retriever(Protocol):
    def search(self, query: str, groups: Sequence[str], k: int) -> list[RetrievedChunk]: ...


@dataclass(frozen=True)
class Citation:
    index: int
    chunk_id: str
    doc_id: str
    title: str
    source_uri: str
    doc_version: str


@dataclass(frozen=True)
class Answer:
    text: str
    refused: bool
    refusal_reason: str | None
    citations: list[Citation]
    grounded: bool
    retrieved: list[RetrievedChunk]
    model: str | None = None
    provider: str | None = None
    usage: Usage = field(default_factory=Usage)
    cost_usd: Decimal | None = Decimal(0)
    timings_ms: dict[str, float] = field(default_factory=dict)
    route: str | None = None
    route_method: str | None = None
    sql: str | None = None
    sql_error: str | None = None


class AnswerService:
    def __init__(
        self,
        retriever: Retriever,
        llm: LLMProvider,
        *,
        k: int = 8,
        min_similarity: float = 0.55,
        max_tokens: int = 1024,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.k = k
        self.min_similarity = min_similarity
        self.max_tokens = max_tokens

    def _refuse(
        self, reason: str, chunks: list[RetrievedChunk], timings: dict[str, float]
    ) -> Answer:
        return Answer(
            text=REFUSAL_MESSAGE,
            refused=True,
            refusal_reason=reason,
            citations=[],
            grounded=False,
            retrieved=chunks,
            timings_ms=timings,
        )

    def answer(
        self,
        question: str,
        principal: Principal,
        *,
        extra_sources: Sequence[RetrievedChunk] = (),
        retrieve: bool = True,
    ) -> Answer:
        """`extra_sources` (e.g. a SQL result) are numbered first, ahead of retrieved chunks."""
        timings: dict[str, float] = {}
        chunks = list(extra_sources)
        if retrieve:
            start = time.perf_counter()
            chunks += self.retriever.search(question, sorted(principal.groups), self.k)
            timings["retrieval"] = (time.perf_counter() - start) * 1000

        if not chunks:
            return self._refuse("no_accessible_sources", chunks, timings)
        if not any(c.similarity >= self.min_similarity or c.lexical_rank for c in chunks):
            return self._refuse("low_relevance", chunks, timings)

        completion = self.llm.complete(
            system=SYSTEM_PROMPT,
            prompt=build_prompt(question, chunks),
            max_tokens=self.max_tokens,
        )
        timings["generation"] = completion.latency_ms
        cost = cost_usd(completion.model, completion.usage)
        billed = {
            "model": completion.model,
            "provider": completion.provider,
            "usage": completion.usage,
            "cost_usd": cost,
        }

        text = completion.text.strip()
        valid, invalid = citation_indexes(text, len(chunks))
        if REFUSAL_SENTINEL in text or not valid:
            # An answer that cites nothing is not grounded, so it is not shown.
            reason = "model_insufficient_evidence" if REFUSAL_SENTINEL in text else "ungrounded"
            return replace(self._refuse(reason, chunks, timings), **billed)  # type: ignore[arg-type]

        citations = [
            Citation(
                index=i,
                chunk_id=chunks[i - 1].chunk_id,
                doc_id=chunks[i - 1].doc_id,
                title=chunks[i - 1].title,
                source_uri=chunks[i - 1].source_uri,
                doc_version=chunks[i - 1].doc_version,
            )
            for i in valid
        ]
        return Answer(
            text=mask_pii(strip_citations(text, invalid)),
            refused=False,
            refusal_reason=None,
            citations=citations,
            grounded=True,
            retrieved=chunks,
            timings_ms=timings,
            **billed,  # type: ignore[arg-type]
        )
