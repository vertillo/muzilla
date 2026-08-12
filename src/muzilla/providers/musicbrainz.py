"""MusicBrainz `MetadataProvider` client (docs/product-spec.md).

MusicBrainz is the highest-trust source: it has the richest tracklist
data (ISRCs, recording MBIDs, per-disc positions) and its own release
IDs are what `changes/` and the UI key off for "this is definitively
that release." No auth is required, but the server-side rate limiter
503s on overlapping requests, hence the hard-locked limiter registered
under "musicbrainz" in `ratelimit.py` — every request here goes through
it even though MB's documented budget is nominally 1 req/sec, because
concurrent (not just fast) requests trigger the 503.
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


class MusicBrainzProvider:
    name = "musicbrainz"
    capabilities = _CAPABILITIES
    requires_auth = False

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def _build_lucene_query(self, query: ReleaseQuery) -> str:
        """Builds a Lucene query from whichever fields this query has —
        MB is the only provider that can use barcode/catalog-number, so
        those get folded in here rather than baked into `ReleaseQuery`
        itself, which stays provider-agnostic."""
        terms: list[str] = []
        if query.album:
            terms.append(f'release:"{_escape_lucene(query.album)}"')
        if query.title:
            terms.append(f'recording:"{_escape_lucene(query.title)}"')
        if query.artist:
            terms.append(f'artist:"{_escape_lucene(query.artist)}"')
        elif query.album_artist:
            terms.append(f'artist:"{_escape_lucene(query.album_artist)}"')
        if query.isrc:
            terms.append(f'isrc:"{_escape_lucene(query.isrc)}"')
        if query.barcode:
            terms.append(f'barcode:"{_escape_lucene(query.barcode)}"')
        if query.catalog_number:
            terms.append(f'catno:"{_escape_lucene(query.catalog_number)}"')
        return " AND ".join(terms)

    async def search_releases(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]:
        lucene = self._build_lucene_query(query)
        if not lucene:
            return []
        try:
            async with get_limiter(self.name):
                response = await self._client.get(
                    "/release",
                    params={"query": lucene, "limit": limit, "fmt": "json"},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise
        payload = response.json()
        return [
            self._candidate_from_search_hit(hit)
            for hit in payload.get("releases", [])[:limit]
        ]

    def _candidate_from_search_hit(self, hit: dict[str, Any]) -> ReleaseCandidate:
        """Search hits carry a subset of `get_release`'s fields (no
        tracklist) — normalize what's here, leave tracks for a
        follow-up `get_release` call."""
        artist_credit = hit.get("artist-credit") or []
        album_artist = artist_credit[0]["name"] if artist_credit else None
        release_group = hit.get("release-group") or {}
        label_info = (hit.get("label-info") or [{}])[0]
        label = (label_info.get("label") or {}).get("name")
        catalog_number = label_info.get("catalog-number")
        return ReleaseCandidate(
            source=self.name,
            ref=ProviderRef(provider=self.name, id=hit["id"]),
            album=hit.get("title"),
            album_artist=album_artist,
            year=_year_from_date(hit.get("date")),
            original_year=_year_from_date(release_group.get("first-release-date")),
            label=label,
            catalog_number=catalog_number,
            barcode=hit.get("barcode"),
            country=hit.get("country"),
            media=hit.get("packaging"),
            track_count=_int_or_none(hit.get("track-count")),
            mb_release_id=hit.get("id"),
            mb_release_group_id=release_group.get("id"),
            raw=hit,
        )

    async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None:
        try:
            async with get_limiter(self.name):
                response = await self._client.get(
                    f"/release/{ref.id}",
                    params={"inc": "recordings+artist-credits+labels+release-groups", "fmt": "json"},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return self._candidate_from_release(response.json())

    def _candidate_from_release(self, payload: dict[str, Any]) -> ReleaseCandidate:
        artist_credit = payload.get("artist-credit") or []
        album_artist = artist_credit[0]["name"] if artist_credit else None
        release_group = payload.get("release-group") or {}
        label_info = (payload.get("label-info") or [{}])[0]
        label = (label_info.get("label") or {}).get("name")
        catalog_number = label_info.get("catalog-number")

        tracks: list[CandidateTrack] = []
        for medium in payload.get("media", []):
            disc_number = medium.get("position")
            for track in medium.get("tracks", []):
                track_artist_credit = track.get("artist-credit") or artist_credit
                track_artist = track_artist_credit[0]["name"] if track_artist_credit else None
                recording = track.get("recording") or {}
                isrcs = recording.get("isrcs") or []
                length = track.get("length") if track.get("length") is not None else recording.get("length")
                tracks.append(
                    CandidateTrack(
                        position=track.get("position", 0),
                        title=track.get("title", ""),
                        artist=track_artist,
                        duration_ms=length,
                        disc_number=disc_number,
                        isrc=isrcs[0] if isrcs else None,
                        mb_track_id=track.get("id"),
                        mb_recording_id=recording.get("id"),
                    )
                )

        return ReleaseCandidate(
            source=self.name,
            ref=ProviderRef(provider=self.name, id=payload["id"]),
            album=payload.get("title"),
            album_artist=album_artist,
            year=_year_from_date(payload.get("date")),
            original_year=_year_from_date(release_group.get("first-release-date")),
            label=label,
            catalog_number=catalog_number,
            barcode=payload.get("barcode"),
            country=payload.get("country"),
            media=payload.get("packaging"),
            track_count=len(tracks),
            tracks=tuple(tracks),
            mb_release_id=payload.get("id"),
            mb_release_group_id=release_group.get("id"),
            art_refs=(),
            raw=payload,
        )

    async def health(self) -> ProviderHealth:
        try:
            async with get_limiter(self.name):
                response = await self._client.get("/release", params={"query": "test", "limit": 1, "fmt": "json"})
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


def _escape_lucene(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = ["MusicBrainzProvider"]
