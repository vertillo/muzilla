"""Pure, network-free metadata value objects.

TrackMeta is the canonical in-memory representation of one track's
metadata — the output of tags/reader.py, the input to tags/writer.py,
and the shape everything else (matching, diffing, API schemas) builds
on. Field names here are deliberately identical to domain.fields names
so the two stay mechanically in sync.
"""

from __future__ import annotations

from dataclasses import astuple, dataclass, field
from hashlib import blake2b


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

    # Art/lyrics presence (not full content — see changes/blobstore.py
    # and providers/lrclib.py for the actual bytes/text). Populated by
    # tags/reader.py's read_track alongside the rest of the probe, so
    # scan doesn't need a second mutagen.File() open per track.
    has_embedded_art: bool = False
    has_lyrics: bool = False
    lyrics_synced: bool = False
    """Always False from read_track's probe — muzilla only writes
    unsynced (plain-text) lyrics (tags/writer.py's write_lyrics), so a
    scanned file can only be detected as having *unsynced* lyrics here.
    True is set explicitly by the enrichment path when it embeds an
    LRCLIB syncedLyrics result, not derived from a probe."""

    # Long tail: raw tag frames not mapped to a canonical field, keyed
    # by their native frame/key name. Never touched by matching or the
    # strip-rules engine unless explicitly targeted.
    extra_tags: dict[str, str] = field(default_factory=dict)


def tag_hash(meta: TrackMeta) -> str:
    """blake2b of the canonical tag serialization.

    The drift-detection primitive used both at scan time (pipeline/
    scan.py) and at apply time (changes/conflicts.py): if a file's
    on-disk tags hash differently than what was staged, something else
    (Picard, foobar, a manual edit) touched the file since staging, and
    the apply path must treat that as a conflict rather than steamroll
    it — see docs/product-spec.md "files are truth" apply-probe step.

    Lives in `domain` (not `pipeline`, which sits above `changes` in
    the layering contract) so both scan and the changes/ package can
    compute the identical hash without either importing the other.
    """
    canonical = repr(astuple(meta)).encode()
    return blake2b(canonical).hexdigest()


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
