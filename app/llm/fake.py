"""Deterministic provider for tests, CI and offline development: no network, no cost.

It behaves like a well-grounded model: it answers from the first source it is given and cites
it, and returns the refusal sentinel when it is given no sources. That keeps the full request
path (prompt building, citation validation, cost accounting) exercised without an LLM.
"""

import html
import re
import time

from app.llm.base import Completion, Usage

_SOURCE = re.compile(r'<source id="(\d+)"[^>]*>\n(.*?)\n</source>', re.DOTALL)


class FakeProvider:
    name = "fake"

    def __init__(self, model: str = "fake-grounded") -> None:
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> Completion:
        start = time.perf_counter()
        sources = _SOURCE.findall(prompt)
        if sources:
            source_id, body = sources[0]
            # Skip the "Title > Heading" context line the chunker prepends.
            lines = [line for line in html.unescape(body).splitlines()[1:] if line.strip()]
            excerpt = " ".join(lines)[:240].strip()
            text = f"According to the documentation, {excerpt} [{source_id}]"
        else:
            text = "INSUFFICIENT_EVIDENCE"
        text = text[: max_tokens * 4]
        usage = Usage(input_tokens=(len(system) + len(prompt)) // 4, output_tokens=len(text) // 4)
        return Completion(
            text=text,
            model=self._model,
            provider=self.name,
            usage=usage,
            stop_reason="end_turn",
            latency_ms=(time.perf_counter() - start) * 1000,
        )
