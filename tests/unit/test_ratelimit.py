import time

import pytest

from app.api.ratelimit import RateLimiter


def test_a_burst_is_allowed_then_refused() -> None:
    limiter = RateLimiter(rate_per_minute=60, burst=3)

    assert [limiter.check("ana") for _ in range(3)] == [0.0, 0.0, 0.0]
    assert limiter.check("ana") > 0


def test_callers_are_limited_independently() -> None:
    limiter = RateLimiter(rate_per_minute=60, burst=1)

    assert limiter.check("ana") == 0.0
    assert limiter.check("bo") == 0.0, "bo has their own bucket"
    assert limiter.check("ana") > 0


def test_the_wait_is_the_time_until_one_token_returns() -> None:
    limiter = RateLimiter(rate_per_minute=60, burst=1)  # one token per second

    limiter.check("ana")

    assert 0 < limiter.check("ana") <= 1


def test_refilled_buckets_are_forgotten_once_the_table_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A fixed clock: a real one made this depend on whether the loop outlasted the refill time.
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    limiter = RateLimiter(rate_per_minute=6000, burst=1)  # refills in 10ms
    for i in range(RateLimiter._SWEEP_AT):
        limiter.check(f"caller-{i}")

    clock[0] += 1.0
    limiter.check("newcomer")

    # Every earlier bucket has refilled by now, so the sweep leaves only the newcomer.
    assert list(limiter._buckets) == ["newcomer"]
