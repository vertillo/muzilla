"""Deezer `MetadataProvider` client.

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

from dataclasses import replace
from typing import Any

import httpx

from muzilla.providers.base import (
    ArtRef,
    CandidateTrack,
    Capability,
    ProviderHealth,
    ProviderRef,
    ReleaseCandidate,
    ReleaseQuery,
)
from muzilla.providers.ratelimit import get_limiter

_CAPABILITIES = frozenset(
    {Capability.SEARCH_RELEASES, Capability.GET_RELEASE, Capability.GET_TRACK}
)


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

    def _build_track_search_queries(self, query: ReleaseQuery) -> list[str]:
        """Return progressively looser track queries, capped by the caller.

        Album search cannot identify a loose single.  Deezer track hits carry
        the containing album ID, which is then hydrated through ``/album``.
        """
        if not query.title and not query.isrc:
            return []
        exact: list[str] = []
        if query.isrc:
            exact.append(f'isrc:"{query.isrc}"')
        if query.title:
            exact.append(f'track:"{query.title}"')
        if query.artist:
            exact.append(f'artist:"{query.artist}"')
        queries = [" ".join(exact)] if exact else []
        if query.title and query.artist:
            queries.append(f'track:"{query.title}" artist:"{query.artist}"')
        if query.title:
            queries.append(f'track:"{query.title}"')
        return list(dict.fromkeys(query_text for query_text in queries if query_text))

    async def search_releases(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]:
        if query.title or query.isrc:
            return await self._search_tracks(query, limit)
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

    async def _search_tracks(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]:
        candidates: list[ReleaseCandidate] = []
        seen: set[str] = set()
        # Three requests is a deliberate provider budget, not an open-ended
        # fallback loop.  Stop as soon as enough unique releases are found.
        for search_query in self._build_track_search_queries(query)[:3]:
            try:
                async with get_limiter(self.name):
                    response = await self._client.get(
                        "/search/track", params={"q": search_query, "limit": limit}
                    )
                    response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                raise
            for hit in response.json().get("data", []):
                candidate = self._candidate_from_track_search_hit(hit)
                if candidate.ref.id not in seen:
                    seen.add(candidate.ref.id)
                    candidates.append(candidate)
                if len(candidates) >= limit:
                    return candidates
            if candidates:
                break
        return candidates

    def _candidate_from_search_hit(self, hit: dict[str, Any]) -> ReleaseCandidate:
        artist = hit.get("artist") or {}
        return ReleaseCandidate(
            source=self.name,
            ref=ProviderRef(provider=self.name, id=str(hit["id"])),
            album=hit.get("title"),
            album_artist=artist.get("name"),
            year=_year_from_date(hit.get("release_date")),
            track_count=_int_or_none(hit.get("nb_tracks")),
            deezer_album_id=str(hit["id"]),
            art_refs=_art_refs(hit),
            raw=hit,
        )

    def _candidate_from_track_search_hit(self, hit: dict[str, Any]) -> ReleaseCandidate:
        album = hit.get("album") or {}
        artist = hit.get("artist") or {}
        representative = CandidateTrack(
            position=_int_or_none(hit.get("track_position")) or 1,
            title=hit.get("title") or "",
            artist=artist.get("name"),
            duration_ms=_seconds_to_ms(hit.get("duration")),
            disc_number=_int_or_none(hit.get("disk_number")),
            isrc=hit.get("isrc"),
        )
        return ReleaseCandidate(
            source=self.name,
            ref=ProviderRef(provider=self.name, id=str(album["id"])),
            album=album.get("title"),
            album_artist=artist.get("name"),
            track_count=_int_or_none(album.get("nb_tracks")),
            deezer_album_id=str(album["id"]),
            candidate_type="track",
            representative_track=representative,
            art_refs=_art_refs(album),
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

    async def get_track_candidate(self, ref: ProviderRef) -> ReleaseCandidate | None:
        """Resolve a Deezer track ID, then hydrate its containing album.

        The public web URL is never requested. Both calls use this adapter's
        configured API client and provider IDs only.
        """
        try:
            async with get_limiter(self.name):
                response = await self._client.get(f"/track/{ref.id}")
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        payload = response.json()
        if payload.get("error"):
            return None
        summary = self._candidate_from_track_search_hit(payload)
        hydrated = await self.get_release(summary.ref)
        if hydrated is None:
            return None
        return replace(
            hydrated,
            candidate_type="track",
            representative_track=summary.representative_track,
        )

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
            track_count=len(tracks),
            tracks=tracks,
            deezer_album_id=str(payload["id"]),
            art_refs=_art_refs(payload),
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


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _art_refs(payload: dict[str, Any]) -> tuple[ArtRef, ...]:
    cover = payload.get("cover_xl") or payload.get("cover_big") or payload.get("cover_medium")
    if not cover:
        return ()
    return (ArtRef(url=str(cover), source="deezer"),)


__all__ = ["DeezerProvider"]
