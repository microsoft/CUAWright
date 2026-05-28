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


# ---- process-global singleton ------------------------------------------------

_global_throttle: AsyncTokenBucket | None = None
_global_lock = asyncio.Lock()


async def get_global_throttle(rate: float, capacity: int = 1) -> AsyncTokenBucket:
    """Return (and lazily create) the process-wide throttle bucket."""
    global _global_throttle
    if _global_throttle is not None:
        return _global_throttle
    async with _global_lock:
        # Double-check after acquiring the lock.
        if _global_throttle is None:
            _global_throttle = AsyncTokenBucket(rate, capacity)
        return _global_throttle


def reset_global_throttle() -> None:
    """Reset the singleton — mainly for tests."""
    global _global_throttle
    _global_throttle = None
