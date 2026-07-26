"""Async token-bucket rate limiting, process-global per provider
(docs/PLAN.md §3).

Buckets are process-global (module-level registry) rather than
per-request or per-client: an import job and an interactive UI search
share the same real rate limit against the same remote API, so they
must share one bucket. Interactive callers pass a higher `priority` to
jump the queue ahead of background import work.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Classic token bucket: `rate` tokens/sec, capacity `burst`."""

    rate: float
    burst: float
    _tokens: float = field(init=False)
    _last: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self._tokens = self.burst
        self._last = time.monotonic()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
                await asyncio.sleep(wait)


@dataclass
class RateLimiter:
    """Wraps a `TokenBucket` with a concurrency semaphore and, for
    providers like MusicBrainz whose server-side limiter 503s on
    overlapping requests even at nominal rate, an optional hard lock
    that serializes requests entirely instead of just throttling them.
    """

    bucket: TokenBucket
    concurrency: int
    hard_lock: bool = False
    _semaphore: asyncio.Semaphore = field(init=False)
    _serialize: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.concurrency)

    async def __aenter__(self) -> RateLimiter:
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.release()

    async def acquire(self) -> None:
        if self.hard_lock:
            await self._serialize.acquire()
        await self._semaphore.acquire()
        await self.bucket.acquire()

    def release(self) -> None:
        self._semaphore.release()
        if self.hard_lock:
            self._serialize.release()


# Limits per docs/PLAN.md §3's table.
_DEFAULT_LIMITERS: dict[str, RateLimiter] = {
    "musicbrainz": RateLimiter(TokenBucket(rate=1.0, burst=1.0), concurrency=1, hard_lock=True),
    "discogs": RateLimiter(TokenBucket(rate=60.0 / 60.0, burst=5.0), concurrency=4),
    "deezer": RateLimiter(TokenBucket(rate=50.0 / 5.0, burst=10.0), concurrency=8),
    "acoustid": RateLimiter(TokenBucket(rate=3.0, burst=3.0), concurrency=2),
    "coverartarchive": RateLimiter(TokenBucket(rate=5.0, burst=5.0), concurrency=4),
    "lrclib": RateLimiter(TokenBucket(rate=10.0, burst=10.0), concurrency=8),
}


def get_limiter(provider: str) -> RateLimiter:
    """Returns the process-global limiter for a provider name.

    Raises `KeyError` for unknown providers rather than silently
    fabricating an unlimited one — a typo'd provider name should fail
    loudly, not bypass rate limiting.
    """
    return _DEFAULT_LIMITERS[provider]
