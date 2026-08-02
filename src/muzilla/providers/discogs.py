"""Discogs `MetadataProvider` client (docs/PLAN.md §3, §8).

Discogs requires a personal access token; the constructor takes it as a
plain `str | None` rather than reaching into `ProviderConfig` itself,
so tests can construct a client with/without a token without touching
config machinery. Per docs/PLAN.md §8 ("missing token degrades
gracefully, provider disabled with a banner, never a crash"), a
missing token does NOT raise at construction time — only when a caller
actually tries to hit the network, since Discogs would 401 anyway and
a local `RuntimeError` with a clear message beats an opaque HTTP error
surfacing from deep in an httpx call.
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


class DiscogsProvider:
    name = "discogs"
    capabilities = _CAPABILITIES
    requires_auth = True

    def __init__(self, client: httpx.AsyncClient, token: str | None) -> None:
        self._client = client
        self._token = token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Discogs token={self._token}"}

    def _build_search_query(self, query: ReleaseQuery) -> str:
        terms: list[str] = []
        if query.title:
            terms.append(query.title)
        elif query.album:
            terms.append(query.album)
        if query.artist:
            terms.append(query.artist)
        elif query.album_artist:
            terms.append(query.album_artist)
        return " ".join(terms)

    async def search_releases(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]:
        if self._token is None:
            raise RuntimeError("discogs: no token configured, cannot query the API")
        search_query = self._build_search_query(query)
        if not search_query:
            return []
        try:
            async with get_limiter(self.name):
                params: dict[str, str | int] = {"q": search_query, "type": "release", "per_page": limit}
                if query.title:
                    params["title"] = query.title
                elif query.album:
                    params["title"] = query.album
                if query.artist or query.album_artist:
                    params["artist"] = query.artist or query.album_artist or ""
                response = await self._client.get(
                    "/database/search",
                    params=params,
                    headers=self._auth_headers(),
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise
        payload = response.json()
        return [
            self._candidate_from_search_hit(hit) for hit in payload.get("results", [])[:limit]
        ]

    def _candidate_from_search_hit(self, hit: dict[str, Any]) -> ReleaseCandidate:
        labels = hit.get("label") or []
        title = hit.get("title", "")
        album_artist, _, album = title.partition(" - ")
        if not album:
            album, album_artist = title, None
        return ReleaseCandidate(
            source=self.name,
            ref=ProviderRef(provider=self.name, id=str(hit["id"])),
            album=album or None,
            album_artist=album_artist or None,
            year=_parse_year(hit.get("year")),
            label=labels[0] if labels else None,
            catalog_number=hit.get("catno"),
            country=hit.get("country"),
            track_count=None,
            discogs_release_id=str(hit["id"]),
            raw=hit,
        )

    async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None:
        if self._token is None:
            raise RuntimeError("discogs: no token configured, cannot query the API")
        try:
            async with get_limiter(self.name):
                response = await self._client.get(
                    f"/releases/{ref.id}", headers=self._auth_headers()
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return self._candidate_from_release(response.json())

    def _candidate_from_release(self, payload: dict[str, Any]) -> ReleaseCandidate:
        artists = payload.get("artists") or []
        album_artist = artists[0]["name"] if artists else None
        labels = payload.get("labels") or []
        label = labels[0].get("name") if labels else None
        catalog_number = labels[0].get("catno") if labels else None
        barcode = next(
            (
                identifier.get("value")
                for identifier in payload.get("identifiers", [])
                if identifier.get("type") == "Barcode"
            ),
            None,
        )
        tracks = tuple(
            CandidateTrack(
                position=index + 1,
                title=track.get("title", ""),
                duration_ms=_parse_duration_ms(track.get("duration")),
            )
            for index, track in enumerate(payload.get("tracklist", []))
        )
        return ReleaseCandidate(
            source=self.name,
            ref=ProviderRef(provider=self.name, id=str(payload["id"])),
            album=payload.get("title"),
            album_artist=album_artist,
            year=payload.get("year") or _year_from_date(payload.get("released")),
            label=label,
            catalog_number=catalog_number,
            barcode=barcode,
            country=payload.get("country"),
            track_count=len(tracks),
            tracks=tracks,
            discogs_release_id=str(payload["id"]),
            raw=payload,
        )

    async def health(self) -> ProviderHealth:
        if self._token is None:
            return ProviderHealth(
                name=self.name, healthy=False, detail="no Discogs token configured"
            )
        try:
            async with get_limiter(self.name):
                response = await self._client.get(
                    "/database/search",
                    params={"q": "test", "type": "release", "per_page": 1},
                    headers=self._auth_headers(),
                )
                response.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            return ProviderHealth(name=self.name, healthy=False, detail=str(exc))
        return ProviderHealth(name=self.name, healthy=True)


def _parse_year(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value)[:4])
    except ValueError:
        return None


def _year_from_date(date: str | None) -> int | None:
    if not date:
        return None
    try:
        return int(date[:4])
    except ValueError:
        return None


def _parse_duration_ms(duration: str | None) -> int | None:
    """Discogs tracklist durations are "MM:SS" strings, sometimes empty."""
    if not duration:
        return None
    parts = duration.split(":")
    try:
        parts_int = [int(p) for p in parts]
    except ValueError:
        return None
    seconds = 0
    for part in parts_int:
        seconds = seconds * 60 + part
    return seconds * 1000


__all__ = ["DiscogsProvider"]
