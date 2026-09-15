"""Claude via the first-party Anthropic API."""

import time
from typing import Any

import anthropic
from anthropic.types import TextBlock

from app.llm.base import Completion, LLMError, LLMTimeout, Usage


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 30,
        max_retries: int = 2,
        client: Any = None,
    ) -> None:
        self._model = model
        # The SDK retries 429/5xx/connection errors with backoff; timeouts bound tail latency.
        self.client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
        )

    @property
    def model(self) -> str:
        return self._model

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> Completion:
        start = time.perf_counter()
        try:
            message = self.client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APITimeoutError as exc:
            raise LLMTimeout(str(exc)) from exc
        except anthropic.APIError as exc:
            raise LLMError(str(exc)) from exc

        usage = message.usage
        return Completion(
            text="".join(block.text for block in message.content if isinstance(block, TextBlock)),
            model=message.model,
            provider=self.name,
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            ),
            stop_reason=message.stop_reason,
            latency_ms=(time.perf_counter() - start) * 1000,
        )
