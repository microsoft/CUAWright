"""Unit tests for webwright.utils.throttle."""

from __future__ import annotations

import asyncio
import time

import pytest

from webwright.utils.throttle import AsyncTokenBucket, get_global_throttle, reset_global_throttle


# ---- AsyncTokenBucket --------------------------------------------------------


def test_constructor_rejects_non_positive_rate() -> None:
    with pytest.raises(ValueError, match="rate must be positive"):
        AsyncTokenBucket(rate=0, capacity=1)
    with pytest.raises(ValueError, match="rate must be positive"):
        AsyncTokenBucket(rate=-1, capacity=1)


def test_constructor_rejects_zero_capacity() -> None:
    with pytest.raises(ValueError, match="capacity must be >= 1"):
        AsyncTokenBucket(rate=1.0, capacity=0)


@pytest.mark.asyncio
async def test_acquire_within_capacity() -> None:
    bucket = AsyncTokenBucket(rate=100.0, capacity=3)
    # Should be able to grab 3 tokens immediately.
    for _ in range(3):
        await bucket.acquire()


@pytest.mark.asyncio
async def test_acquire_blocks_when_empty() -> None:
    bucket = AsyncTokenBucket(rate=20.0, capacity=1)
    await bucket.acquire()  # drain the single token

    start = time.monotonic()
    await bucket.acquire()  # must wait ~0.05s for refill
    elapsed = time.monotonic() - start

    assert elapsed >= 0.03, f"Expected to block ~50ms, but took only {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_burst_capacity_replenishes() -> None:
    bucket = AsyncTokenBucket(rate=1000.0, capacity=5)
    # Drain all 5 tokens.
    for _ in range(5):
        await bucket.acquire()
    # After a short sleep tokens should have been added back.
    await asyncio.sleep(0.01)
    await bucket.acquire()


# ---- Singleton ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    reset_global_throttle()
    yield  # type: ignore[misc]
    reset_global_throttle()


@pytest.mark.asyncio
async def test_singleton_returns_same_instance() -> None:
    a = await get_global_throttle(10.0, 2)
    b = await get_global_throttle(10.0, 2)
    assert a is b


@pytest.mark.asyncio
async def test_reset_clears_singleton() -> None:
    a = await get_global_throttle(10.0, 2)
    reset_global_throttle()
    b = await get_global_throttle(10.0, 2)
    assert a is not b
