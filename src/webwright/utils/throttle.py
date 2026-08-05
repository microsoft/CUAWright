"""Process-global async token bucket for throttling model API calls."""

from __future__ import annotations

import asyncio
import time


class AsyncTokenBucket:
    """Classic token bucket rate limiter for ``asyncio`` callers.

    Parameters
    ----------
    rate:
        Tokens added per second (refill rate).
    capacity:
        Maximum burst size (bucket depth).  Defaults to ``1``.
    """

    def __init__(self, rate: float, capacity: int = 1) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.rate = rate
        self.capacity = capacity
        self._tokens: float = float(capacity)
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # How long until at least one token is available?
                wait = (1.0 - self._tokens) / self.rate
            await asyncio.sleep(wait)


# ---- process-global registry -------------------------------------------------

_throttle_registry: dict[tuple[float, int], AsyncTokenBucket] = {}
_registry_lock = asyncio.Lock()


async def get_global_throttle(rate: float, capacity: int = 1) -> AsyncTokenBucket:
    """Return (and lazily create) a throttle bucket for the given config.

    Each unique ``(rate, capacity)`` pair receives its own bucket so that
    different model configurations coexisting in the same process are
    throttled independently.
    """
    key = (rate, capacity)
    bucket = _throttle_registry.get(key)
    if bucket is not None:
        return bucket
    async with _registry_lock:
        # Double-check after acquiring the lock.
        if key not in _throttle_registry:
            _throttle_registry[key] = AsyncTokenBucket(rate, capacity)
        return _throttle_registry[key]


def reset_global_throttle() -> None:
    """Clear the registry — mainly for tests."""
    _throttle_registry.clear()
