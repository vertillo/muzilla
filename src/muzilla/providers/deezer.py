"""Deezer `MetadataProvider` client (docs/PLAN.md §3).

Deezer is a lighter-weight, no-auth fallback source: no barcode/catno
search, no ISRC or MusicBrainz IDs on the album payload, and its
`release_date` is the only date field available — there's no separate
"original release" concept like MusicBrainz's release-group first-
release-date. We deliberately do NOT copy `release_date`'s year into
`original_year`: Deezer's `release_date` is per-edition (often a
reissue date), and a confident `original_year` should come from a
provider that actually distinguishes the two. Leaving it None here is
consistent with "don't fabricate," even though it means `year` is the
only date field this provider ever populates.
"""

from __future__ import annotations

from typing import Any

import httpx

from muzilla.providers.base import (
    CandidateTrack,
    Capability,
    ProviderHealth,
    ProviderRef,
    ReleaseCandidate,
    ReleaseQuery,
)
from muzilla.providers.ratelimit import get_limiter

_CAPABILITIES = frozenset({Capability.SEARCH_RELEASES, Capability.GET_RELEASE})


class DeezerProvider:
    name = "deezer"
    capabilities = _CAPABILITIES
    requires_auth = False

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def _build_search_query(self, query: ReleaseQuery) -> str:
        terms: list[str] = []
        if query.album:
            terms.append(f'album:"{query.album}"')
        if query.artist:
            terms.append(f'artist:"{query.artist}"')
        elif query.album_artist:
            terms.append(f'artist:"{query.album_artist}"')
        return " ".join(terms)

    async def search_releases(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]:
        search_query = self._build_search_query(query)
        if not search_query:
            return []
        try:
            async with get_limiter(self.name):
                response = await self._client.get(
                    "/search/album", params={"q": search_query, "limit": limit}
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise
        payload = response.json()
        return [self._candidate_from_search_hit(hit) for hit in payload.get("data", [])[:limit]]

    def _candidate_from_search_hit(self, hit: dict[str, Any]) -> ReleaseCandidate:
        artist = hit.get("artist") or {}
        return ReleaseCandidate(
            source=self.name,
            ref=ProviderRef(provider=self.name, id=str(hit["id"])),
            album=hit.get("title"),
            album_artist=artist.get("name"),
            year=_year_from_date(hit.get("release_date")),
            deezer_album_id=str(hit["id"]),
            raw=hit,
        )

    async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None:
        try:
            async with get_limiter(self.name):
                response = await self._client.get(f"/album/{ref.id}")
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        payload = response.json()
        if payload.get("error"):
            return None
        return self._candidate_from_album(payload)

    def _candidate_from_album(self, payload: dict[str, Any]) -> ReleaseCandidate:
        artist = payload.get("artist") or {}
        tracks_data = (payload.get("tracks") or {}).get("data", [])
        tracks = tuple(
            CandidateTrack(
                position=track.get("track_position", index + 1),
                title=track.get("title", ""),
                artist=(track.get("artist") or {}).get("name"),
                duration_ms=_seconds_to_ms(track.get("duration")),
                disc_number=track.get("disk_number"),
            )
            for index, track in enumerate(tracks_data)
        )
        return ReleaseCandidate(
            source=self.name,
            ref=ProviderRef(provider=self.name, id=str(payload["id"])),
            album=payload.get("title"),
            album_artist=artist.get("name"),
            year=_year_from_date(payload.get("release_date")),
            barcode=payload.get("upc"),
            tracks=tracks,
            deezer_album_id=str(payload["id"]),
            raw=payload,
        )

    async def health(self) -> ProviderHealth:
        try:
            async with get_limiter(self.name):
                response = await self._client.get("/search/album", params={"q": "test", "limit": 1})
                response.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            return ProviderHealth(name=self.name, healthy=False, detail=str(exc))
        return ProviderHealth(name=self.name, healthy=True)


def _year_from_date(date: str | None) -> int | None:
    if not date:
        return None
    try:
        return int(date[:4])
    except ValueError:
        return None


def _seconds_to_ms(seconds: int | None) -> int | None:
    if seconds is None:
        return None
    return seconds * 1000


__all__ = ["DeezerProvider"]
