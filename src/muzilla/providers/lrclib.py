"""LRCLIB `LyricsProvider` client (docs/PLAN.md §3).

LRCLIB's `/get` endpoint does exact artist/title (+ optional
album/duration) matching and 404s on no match — that maps directly to
returning `None`. `syncedLyrics` (LRC time-tagged) is preferred over
`plainLyrics` whenever both are present, since muzilla only stores the
lyrics text itself but a synced version is strictly more useful to
downstream players that understand LRC.
"""

from __future__ import annotations

import httpx

from muzilla.providers.base import Capability, ProviderHealth
from muzilla.providers.ratelimit import get_limiter

_CAPABILITIES = frozenset({Capability.LYRICS})


class LrcLibProvider:
    name = "lrclib"
    capabilities = _CAPABILITIES
    requires_auth = False

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_lyrics(self, artist: str, title: str, duration_ms: int | None) -> str | None:
        params: dict[str, str | int] = {"artist_name": artist, "track_name": title}
        if duration_ms is not None:
            params["duration"] = round(duration_ms / 1000)
        try:
            async with get_limiter(self.name):
                response = await self._client.get("/get", params=params)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        payload = response.json()
        synced = payload.get("syncedLyrics")
        if synced:
            return str(synced)
        plain = payload.get("plainLyrics")
        if plain:
            return str(plain)
        return None

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
