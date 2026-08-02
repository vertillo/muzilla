"""Provider Protocols and the normalized query/candidate shapes they
exchange (docs/PLAN.md §3, "Provider abstraction").

Four narrow Protocols rather than one fat one, so Cover Art Archive
doesn't have to stub `search_releases`. Providers are dumb: they never
score or decide, they just translate a normalized query into normalized
candidates. All ranking lives in `matching/`.

`ReleaseQuery` must never be shaped by the richest provider — fields a
given source can't use (Deezer has no barcode search) are simply
ignored by that provider's implementation.

`ReleaseCandidate` always carries the `source` it came from. A
candidate is one release from one provider (docs/PLAN.md §3's "one
release, one source" rule) — staging it applies its fields wholesale,
never merged field-by-field with another candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from muzilla.domain.metadata import LyricsResult


class Capability(Enum):
    SEARCH_RELEASES = "search_releases"
    GET_RELEASE = "get_release"
    ART = "art"
    LYRICS = "lyrics"
    FINGERPRINT_LOOKUP = "fingerprint_lookup"


@dataclass(frozen=True, slots=True)
class ProviderRef:
    """Points at one release/recording at one provider — the identifier
    a `ReleaseCandidate` carries and a changeset's `candidate_ref` stores."""

    provider: str
    id: str


@dataclass(frozen=True, slots=True)
class ReleaseQuery:
    """Normalized search input. Providers use what they can and ignore
    the rest — never the other way around."""

    album: str | None = None
    album_artist: str | None = None
    artist: str | None = None
    title: str | None = None
    """Set for singleton/recording queries; None for release queries."""
    track_count: int | None = None
    year: int | None = None
    barcode: str | None = None
    catalog_number: str | None = None
    isrc: str | None = None
    duration_ms: int | None = None
    """Approximate recording duration, used by singleton queries to
    disambiguate same-titled recordings."""


@dataclass(frozen=True, slots=True)
class CandidateTrack:
    """One track on a candidate release, as reported by the provider."""

    position: int
    title: str
    artist: str | None = None
    duration_ms: int | None = None
    disc_number: int | None = None
    isrc: str | None = None
    mb_track_id: str | None = None
    mb_recording_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArtRef:
    url: str
    source: str
    width: int | None = None
    height: int | None = None
    mime: str | None = None


@dataclass(frozen=True, slots=True)
class ReleaseCandidate:
    """A normalized release (or, for singleton queries, a single
    recording credited to a release) from exactly one provider.

    Every tag a user accepts by picking this candidate comes from this
    object — see docs/PLAN.md §3's rejection of per-field merging.
    """

    source: str
    ref: ProviderRef
    album: str | None
    album_artist: str | None
    year: int | None = None
    original_year: int | None = None
    label: str | None = None
    catalog_number: str | None = None
    barcode: str | None = None
    country: str | None = None
    media: str | None = None
    is_compilation: bool = False
    track_count: int | None = None
    """Count declared by the provider summary or hydrated release.

    Search summaries do not have a tracklist.  Consumers must use this field
    rather than deriving a zero count from ``tracks`` before hydration.
    """
    tracks: tuple[CandidateTrack, ...] = ()
    candidate_type: str = "release"
    """``release`` normally; ``track`` when a track search led to its release."""
    representative_track: CandidateTrack | None = None
    mb_release_id: str | None = None
    mb_release_group_id: str | None = None
    discogs_release_id: str | None = None
    deezer_album_id: str | None = None
    external_ids: dict[str, str] = field(default_factory=dict)
    art_refs: tuple[ArtRef, ...] = ()
    raw: dict[str, object] = field(default_factory=dict)
    """Provider's raw normalized payload, kept for debugging/caching —
    never surfaced directly in the UI or applied as tags."""


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    name: str
    healthy: bool
    detail: str = ""


class MetadataProvider(Protocol):
    name: str
    capabilities: frozenset[Capability]
    requires_auth: bool

    async def search_releases(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]: ...

    async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None: ...

    async def health(self) -> ProviderHealth: ...


class ArtProvider(Protocol):
    name: str
    capabilities: frozenset[Capability]
    requires_auth: bool

    async def get_art(self, ref: ProviderRef) -> list[ArtRef]: ...

    async def health(self) -> ProviderHealth: ...


class LyricsProvider(Protocol):
    name: str
    capabilities: frozenset[Capability]
    requires_auth: bool

    async def get_lyrics(
        self, artist: str, title: str, duration_ms: int | None
    ) -> LyricsResult | None: ...

    async def health(self) -> ProviderHealth: ...


class FingerprintProvider(Protocol):
    name: str
    capabilities: frozenset[Capability]
    requires_auth: bool

    async def lookup(self, fingerprint: str, duration_s: float) -> list[FingerprintMatch]: ...

    async def health(self) -> ProviderHealth: ...


@dataclass(frozen=True, slots=True)
class FingerprintMatch:
    mb_recording_id: str
    mb_release_ids: tuple[str, ...]
    score: float
    """AcoustID's own confidence score in [0, 1]."""
