"""A circuit breaker around any provider, so a dead model does not stall every request.

The SDKs already retry transient faults: botocore retries Bedrock with adaptive backoff, and
the Anthropic client retries its own. What they cannot do is notice that the provider has been
failing for a minute and stop paying the timeout on every new request. That is this class's
only job — fail fast while the provider is down, and let exactly one request through to find
out when it comes back.

Requests are served on FastAPI's threadpool, so the state is guarded by a lock.
"""

import threading
import time

from app.llm.base import Completion, LLMError, LLMProvider


class CircuitOpen(LLMError):
    """The provider is being given time to recover; no call was attempted."""


class CircuitBreaker:
    """Wraps a provider, counting consecutive failures.

    After `failure_threshold` consecutive failures the circuit opens and calls raise
    immediately for `reset_seconds`. Calls after that are trials: one success closes the
    circuit, one failure starts the wait again. Concurrent requests can send more than one
    trial, which is deliberate — serialising them would make every recovery wait a round trip.
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        failure_threshold: int = 5,
        reset_seconds: float = 30.0,
    ) -> None:
        self._provider = provider
        self._threshold = failure_threshold
        self._reset_seconds = reset_seconds
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def name(self) -> str:
        return self._provider.name

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            return "open" if time.monotonic() - self._opened_at < self._reset_seconds else "half"

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> Completion:
        if self.state == "open":
            raise CircuitOpen(f"{self.name} is unavailable; retry shortly")
        try:
            completion = self._provider.complete(
                system=system, prompt=prompt, max_tokens=max_tokens
            )
        except LLMError:
            with self._lock:
                self._failures += 1
                if self._failures >= self._threshold:
                    self._opened_at = time.monotonic()
            raise
        with self._lock:
            self._failures = 0
            self._opened_at = None
        return completion
