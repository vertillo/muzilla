"""LRCLIB `LyricsProvider` client (docs/PLAN.md §3).

LRCLIB's `/get` endpoint does exact artist/title (+ optional
album/duration) matching and 404s on no match — that maps directly to
returning `None`. `syncedLyrics` (LRC time-tagged) is preferred over
`plainLyrics` whenever both are present, since a synced version is
strictly more useful to downstream players that understand LRC.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from muzilla.domain.metadata import LyricsResult
from muzilla.providers.base import Capability, ProviderHealth
from muzilla.providers.errors import ProviderPermanentError, ProviderTransientError
from muzilla.providers.ratelimit import get_limiter

_CAPABILITIES = frozenset({Capability.LYRICS})


class LrcLibProvider:
    name = "lrclib"
    capabilities = _CAPABILITIES
    requires_auth = False

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_attempts: int = 3,
        base_backoff_seconds: float = 0.25,
        max_backoff_seconds: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self._client = client
        self._max_attempts = max_attempts
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._sleep = sleep
        self._random_value = random_value

    async def get_lyrics(
        self, artist: str, title: str, duration_ms: int | None
    ) -> LyricsResult | None:
        params: dict[str, str | int] = {"artist_name": artist, "track_name": title}
        if duration_ms is not None:
            params["duration"] = round(duration_ms / 1000)
        response = await self._request_with_retry(params)
        if response is None:  # HTTP 404: an expected absence, not a failure.
            return None
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderPermanentError("lrclib returned an invalid response") from exc
        synced = payload.get("syncedLyrics")
        if synced:
            return LyricsResult(text=str(synced), synced=True, source=self.name)
        plain = payload.get("plainLyrics")
        if plain:
            return LyricsResult(text=str(plain), synced=False, source=self.name)
        return None

    async def _request_with_retry(
        self, params: dict[str, str | int]
    ) -> httpx.Response | None:
        for attempt in range(self._max_attempts):
            retry_after: float | None = None
            try:
                async with get_limiter(self.name):
                    response = await self._client.get("/get", params=params)
            except httpx.RequestError as exc:
                error = ProviderTransientError(f"lrclib request failed: {exc}")
            else:
                if response.status_code == 404:
                    return None
                if response.status_code in (408, 429) or response.status_code >= 500:
                    retry_after = _retry_after_seconds(response)
                    error = ProviderTransientError(
                        f"lrclib temporarily unavailable (HTTP {response.status_code})"
                    )
                elif response.status_code >= 400:
                    raise ProviderPermanentError(f"lrclib request failed (HTTP {response.status_code})")
                else:
                    return response

            if attempt + 1 == self._max_attempts:
                raise error
            await self._sleep(self._retry_delay(attempt, retry_after))
        raise AssertionError("unreachable retry loop")

    def _retry_delay(self, attempt: int, retry_after: float | None) -> float:
        capped = min(self._base_backoff_seconds * (2**attempt), self._max_backoff_seconds)
        # Full jitter prevents independently retried bulk items from forming
        # a thundering herd. Retry-After is a server-provided minimum.
        jittered = capped * self._random_value()
        return float(max(jittered, retry_after or 0.0))

    async def health(self) -> ProviderHealth:
        try:
            async with get_limiter(self.name):
                response = await self._client.get(
                    "/get", params={"artist_name": "health", "track_name": "check"}
                )
                if response.status_code not in (200, 404):
                    response.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            return ProviderHealth(name=self.name, healthy=False, detail=str(exc))
        return ProviderHealth(name=self.name, healthy=True)


__all__ = ["LrcLibProvider"]


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
