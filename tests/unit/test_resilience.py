import pytest

from app.llm.base import Completion, LLMError, Usage
from app.llm.resilience import CircuitBreaker, CircuitOpen


class FlakyProvider:
    name = "flaky"
    model = "test-model"

    def __init__(self, failures: int) -> None:
        self.remaining_failures = failures
        self.calls = 0

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> Completion:
        self.calls += 1
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise LLMError("provider down")
        return Completion("ok", self.model, self.name, Usage(1, 1), "end_turn", 1.0)


def _call(breaker: CircuitBreaker) -> Completion:
    return breaker.complete(system="s", prompt="p", max_tokens=16)


def test_failures_below_the_threshold_leave_the_circuit_closed() -> None:
    provider = FlakyProvider(failures=2)
    breaker = CircuitBreaker(provider, failure_threshold=3)

    for _ in range(2):
        with pytest.raises(LLMError):
            _call(breaker)

    assert breaker.state == "closed"
    assert _call(breaker).text == "ok"
    assert provider.calls == 3


def test_a_success_resets_the_failure_count() -> None:
    provider = FlakyProvider(failures=1)
    breaker = CircuitBreaker(provider, failure_threshold=2)

    with pytest.raises(LLMError):
        _call(breaker)
    _call(breaker)
    provider.remaining_failures = 1
    with pytest.raises(LLMError):
        _call(breaker)

    assert breaker.state == "closed", "the failures were not consecutive"


def test_the_open_circuit_fails_fast_without_calling_the_provider() -> None:
    provider = FlakyProvider(failures=99)
    breaker = CircuitBreaker(provider, failure_threshold=2, reset_seconds=60)

    for _ in range(2):
        with pytest.raises(LLMError):
            _call(breaker)
    assert breaker.state == "open"

    with pytest.raises(CircuitOpen):
        _call(breaker)
    assert provider.calls == 2, "no request was sent while the circuit was open"


def test_after_the_reset_window_one_success_closes_the_circuit() -> None:
    provider = FlakyProvider(failures=2)
    breaker = CircuitBreaker(provider, failure_threshold=2, reset_seconds=0)

    for _ in range(2):
        with pytest.raises(LLMError):
            _call(breaker)

    assert breaker.state == "half"
    assert _call(breaker).text == "ok"
    assert breaker.state == "closed"
