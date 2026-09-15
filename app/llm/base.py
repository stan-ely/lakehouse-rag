"""Provider-neutral completion interface; every provider reports usage in the same shape."""

from dataclasses import dataclass
from typing import Protocol


class LLMError(Exception):
    """The provider failed; the request can be retried or degraded, never silently ignored."""


class LLMTimeout(LLMError):
    pass


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    provider: str
    usage: Usage
    stop_reason: str | None
    latency_ms: float


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> Completion: ...
