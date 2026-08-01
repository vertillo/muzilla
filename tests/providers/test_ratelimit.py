from __future__ import annotations

import asyncio
import time

import pytest

from muzilla.providers.ratelimit import RateLimiter, TokenBucket, get_limiter


@pytest.mark.asyncio
async def test_token_bucket_allows_burst_then_throttles() -> None:
    bucket = TokenBucket(rate=100.0, burst=2.0)
    start = time.monotonic()
    await bucket.acquire()
    await bucket.acquire()
    burst_elapsed = time.monotonic() - start
    assert burst_elapsed < 0.05  # both from the initial burst, no waiting

    await bucket.acquire()  # bucket now empty, must wait for refill
    throttled_elapsed = time.monotonic() - start
    assert throttled_elapsed >= 0.01


@pytest.mark.asyncio
async def test_rate_limiter_concurrency_cap() -> None:
    limiter = RateLimiter(TokenBucket(rate=1000.0, burst=1000.0), concurrency=2)
    in_flight = 0
    max_in_flight = 0

    async def task() -> None:
        nonlocal in_flight, max_in_flight
        async with limiter:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1

    await asyncio.gather(*(task() for _ in range(5)))
    assert max_in_flight <= 2


@pytest.mark.asyncio
async def test_rate_limiter_hard_lock_fully_serializes() -> None:
    limiter = RateLimiter(TokenBucket(rate=1000.0, burst=1000.0), concurrency=5, hard_lock=True)
    in_flight = 0
    max_in_flight = 0

    async def task() -> None:
        nonlocal in_flight, max_in_flight
        async with limiter:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1

    await asyncio.gather(*(task() for _ in range(4)))
    assert max_in_flight == 1


@pytest.mark.asyncio
async def test_cancelled_acquire_releases_resources_for_the_next_runtime_probe() -> None:
    bucket = TokenBucket(rate=0.01, burst=1.0)
    await bucket.acquire()  # make the next acquire wait inside TokenBucket
    limiter = RateLimiter(bucket, concurrency=1, hard_lock=True)
    task = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert limiter._semaphore.locked() is False
    assert limiter._serialize.locked() is False


def test_get_limiter_returns_process_global_singleton() -> None:
    assert get_limiter("musicbrainz") is get_limiter("musicbrainz")


def test_get_limiter_musicbrainz_is_hard_locked() -> None:
    assert get_limiter("musicbrainz").hard_lock is True


def test_get_limiter_unknown_provider_raises() -> None:
    with pytest.raises(KeyError):
        get_limiter("not-a-real-provider")
