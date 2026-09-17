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


def test_refilled_buckets_are_forgotten_once_the_table_grows() -> None:
    limiter = RateLimiter(rate_per_minute=6000, burst=1)
    for i in range(RateLimiter._SWEEP_AT + 1):
        limiter.check(f"caller-{i}")

    # Every bucket refills in 10ms, so the sweep leaves only the most recent callers.
    assert len(limiter._buckets) < RateLimiter._SWEEP_AT
