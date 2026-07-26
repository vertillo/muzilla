"""Pure, network-free metadata value objects.

TrackMeta is the canonical in-memory representation of one track's
metadata — the output of tags/reader.py, the input to tags/writer.py,
and the shape everything else (matching, diffing, API schemas) builds
on. Field names here are deliberately identical to domain.fields names
so the two stay mechanically in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TrackMeta:
    # Identity
    title: str | None = None
    artist: str | None = None
    artists: tuple[str, ...] = ()
    album: str | None = None
    album_artist: str | None = None
    composer: str | None = None
    track_no: int | None = None
    track_total: int | None = None
    disc_no: int | None = None
    disc_total: int | None = None
    year: int | None = None
    original_year: int | None = None
    date: str | None = None
    compilation: bool = False

    # Release
    label: str | None = None
    catalog_number: str | None = None
    barcode: str | None = None
    isrc: str | None = None
    country: str | None = None
    media: str | None = None

    # Classification
    genre: tuple[str, ...] = ()
    mood: tuple[str, ...] = ()
    bpm: int | None = None
    key: str | None = None

    # Provider IDs
    mb_track_id: str | None = None
    mb_release_id: str | None = None
    mb_recording_id: str | None = None
    mb_artist_id: str | None = None
    discogs_release_id: str | None = None
    deezer_track_id: str | None = None
    acoustid_id: str | None = None

    # Audio analysis
    acoustid_fingerprint: str | None = None
    rg_track_gain: float | None = None
    rg_track_peak: float | None = None
    rg_album_gain: float | None = None
    rg_album_peak: float | None = None
    r128_track_gain: float | None = None

    # Technical (read-only; populated by tags/reader.py's probe, never
    # written back by tags/writer.py)
    duration_ms: int | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    codec: str | None = None

    # Admin
    comment: str | None = None
    encoder: str | None = None

    # Long tail: raw tag frames not mapped to a canonical field, keyed
    # by their native frame/key name. Never touched by matching or the
    # strip-rules engine unless explicitly targeted.
    extra_tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtworkRef:
    mime: str
    width: int | None
    height: int | None
    size_bytes: int
    source: str
    """Where this artwork came from: 'embedded', a provider name, or a file path."""


@dataclass(frozen=True, slots=True)
class LyricsResult:
    text: str
    synced: bool
    source: str
