"""A per-caller token bucket for `/query`.

Each answer costs a model call, so an unbounded caller is a bill as well as a load problem.
The bucket refills at a steady rate and allows a burst, which suits interactive use: a person
clicking through a few questions is never throttled, a loop is.

The state is in process, so the limit is per replica: two API containers allow twice the rate.
That is the right trade here — a shared counter would put Redis on the request path for every
query — and the deployment sizes the limit accordingly. A fleet that needs an exact global
limit should move this behind the load balancer or into a shared store.
"""

import threading
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """`rate_per_minute` sustained requests per key, bursting up to `burst`."""

    # Above this many tracked keys, buckets that have refilled are dropped: a full bucket is
    # indistinguishable from a caller that has never been seen, so forgetting it changes nothing.
    _SWEEP_AT = 10_000

    def __init__(self, rate_per_minute: float, burst: int) -> None:
        self._rate = rate_per_minute / 60.0
        self._burst = float(burst)
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}

    def _sweep(self, now: float) -> None:
        full = now - self._burst / self._rate
        self._buckets = {k: b for k, b in self._buckets.items() if b.updated > full}

    def check(self, key: str) -> float:
        """Consumes a token; returns 0 when allowed, or the seconds to wait when not."""
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) >= self._SWEEP_AT:
                self._sweep(now)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = self._buckets[key] = _Bucket(self._burst, now)
            bucket.tokens = min(self._burst, bucket.tokens + (now - bucket.updated) * self._rate)
            bucket.updated = now
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return 0.0
            return (1 - bucket.tokens) / self._rate
