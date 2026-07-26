"""Library analysis report.

In a flat folder with tag quality that varies by era, nobody actually
knows what's in the library. This report is cheap to compute on top of
the scan and gives a first honest picture: how much looks like albums
vs. loose singles, how complete the tags are, and where the likely
duplicates are. It drives Phase 2 grouping tuning but stands alone as
`muzilla analyze` / a dashboard panel in Phase 1.

Album/single split here is a **heuristic on tags alone** — grouping by
`(album, album_artist)` — not the real grouping cascade (fingerprint +
release-id stages) that lands in Phase 2 with `track_groups`. Good
enough to answer "how much of this library is albums" without waiting
on the full pipeline.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.domain.fields import FIELDS

_ALBUM_SPLIT_FIELDS = ("album", "album_artist")


@dataclass(frozen=True, slots=True)
class FieldCompleteness:
    field: str
    label: str
    present: int
    missing: int

    @property
    def total(self) -> int:
        return self.present + self.missing

    @property
    def present_ratio(self) -> float:
        return self.present / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """Tracks that look like the same recording present more than once —
    e.g. the same title/artist ripped at different bitrates. Heuristic
    only: exact (artist, title) match. Fingerprint-based duplicate
    detection is a Phase 6 job."""

    artist: str
    title: str
    track_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FormatBreakdown:
    format: str
    count: int


@dataclass(frozen=True, slots=True)
class LibraryAnalysis:
    total_tracks: int
    tracks_with_errors: int
    tracks_missing: int

    album_track_count: int
    singleton_track_count: int
    """Tracks with no usable (album, album_artist) tag pair — cannot be
    grouped into an album by tags alone."""
    inferred_album_count: int
    """Distinct (album, album_artist) pairs among album_track_count tracks."""

    field_completeness: tuple[FieldCompleteness, ...]

    duplicate_groups: tuple[DuplicateGroup, ...]

    format_breakdown: tuple[FormatBreakdown, ...]
    avg_bitrate: float | None


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, tuple):
        return len(value) == 0
    return False


def analyze_library(session: Session) -> LibraryAnalysis:
    tracks = list(session.scalars(select(Track).where(Track.missing_since.is_(None))))
    tracks_missing = session.scalar(
        select(func.count()).select_from(Track).where(Track.missing_since.is_not(None))
    ) or 0

    total_tracks = len(tracks)
    tracks_with_errors = sum(1 for t in tracks if t.probe_error is not None)

    album_groups: defaultdict[tuple[str, str], list[int]] = defaultdict(list)
    singleton_count = 0
    for t in tracks:
        if _is_blank(t.album) or _is_blank(t.album_artist):
            singleton_count += 1
            continue
        assert t.album_artist is not None and t.album is not None
        album_groups[(t.album_artist, t.album)].append(t.id)

    album_track_count = total_tracks - singleton_count

    completeness = []
    for name, field_def in FIELDS.items():
        if not hasattr(Track, name):
            continue
        present = sum(1 for t in tracks if not _is_blank(getattr(t, name)))
        completeness.append(
            FieldCompleteness(
                field=name,
                label=field_def.label,
                present=present,
                missing=total_tracks - present,
            )
        )

    dupe_key_to_ids: defaultdict[tuple[str, str], list[int]] = defaultdict(list)
    for t in tracks:
        if _is_blank(t.artist) or _is_blank(t.title):
            continue
        key = (t.artist.strip().casefold(), t.title.strip().casefold())  # type: ignore[union-attr]
        dupe_key_to_ids[key].append(t.id)

    duplicate_groups = tuple(
        DuplicateGroup(artist=key[0], title=key[1], track_ids=tuple(ids))
        for key, ids in sorted(dupe_key_to_ids.items())
        if len(ids) > 1
    )

    format_counts = Counter(t.format for t in tracks if t.format is not None)
    format_breakdown = tuple(
        FormatBreakdown(format=fmt, count=count)
        for fmt, count in sorted(format_counts.items(), key=lambda kv: -kv[1])
    )

    bitrates = [t.bitrate for t in tracks if t.bitrate is not None]
    avg_bitrate = sum(bitrates) / len(bitrates) if bitrates else None

    return LibraryAnalysis(
        total_tracks=total_tracks,
        tracks_with_errors=tracks_with_errors,
        tracks_missing=tracks_missing,
        album_track_count=album_track_count,
        singleton_track_count=singleton_count,
        inferred_album_count=len(album_groups),
        field_completeness=tuple(completeness),
        duplicate_groups=duplicate_groups,
        format_breakdown=format_breakdown,
        avg_bitrate=avg_bitrate,
    )
