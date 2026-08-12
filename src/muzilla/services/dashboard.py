"""Cheap library-health summary for the Dashboard (`/`): counts,
album/single split, recent changesets, jobs, provider health, and library
health.

Deliberately separate from `services/analyze.py`'s `analyze_library`:
that report loads every non-missing Track row into Python to compute
field-completeness/duplicate-candidate detail, which is the right cost
for an on-demand `muzilla analyze` report but far too heavy for a
summary that loads on every visit to `/`. Everything here is a single
SQL COUNT/GROUP BY aggregate — no full-table row load.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.db.models import Track, TrackGroup


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    total_tracks: int
    tracks_missing: int
    tracks_with_errors: int
    tracks_missing_art: int
    album_count: int
    """TrackGroup rows of kind in (album, partial_album) — the real,
    cascade-derived grouping, not analyze_library's
    tag-only heuristic."""
    singleton_count: int
    """TrackGroup rows of kind == singleton."""
    ungrouped_track_count: int
    """Tracks with no group_id at all — not yet touched by a cascade run."""


def get_dashboard_summary(session: Session) -> DashboardSummary:
    total_tracks = session.scalar(
        select(func.count()).select_from(Track).where(Track.missing_since.is_(None))
    ) or 0
    tracks_missing = session.scalar(
        select(func.count()).select_from(Track).where(Track.missing_since.is_not(None))
    ) or 0
    tracks_with_errors = session.scalar(
        select(func.count())
        .select_from(Track)
        .where(Track.missing_since.is_(None), Track.probe_error.is_not(None))
    ) or 0
    tracks_missing_art = session.scalar(
        select(func.count())
        .select_from(Track)
        .where(Track.missing_since.is_(None), Track.has_embedded_art.is_(False))
    ) or 0

    group_kind_counts: dict[str, int] = dict(
        session.execute(select(TrackGroup.kind, func.count()).group_by(TrackGroup.kind)).tuples().all()
    )
    album_count = group_kind_counts.get("album", 0) + group_kind_counts.get("partial_album", 0)
    singleton_count = group_kind_counts.get("singleton", 0)

    ungrouped_track_count = session.scalar(
        select(func.count())
        .select_from(Track)
        .where(Track.missing_since.is_(None), Track.group_id.is_(None))
    ) or 0

    return DashboardSummary(
        total_tracks=total_tracks,
        tracks_missing=tracks_missing,
        tracks_with_errors=tracks_with_errors,
        tracks_missing_art=tracks_missing_art,
        album_count=album_count,
        singleton_count=singleton_count,
        ungrouped_track_count=ungrouped_track_count,
    )
