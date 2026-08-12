"""Duplicate-group service: the only way api/cli list, dismiss, or
trigger detection for fingerprint-based duplicates (docs/product-spec.md
§Phase-6). Returns plain dataclasses, never db.models rows — same
boundary discipline as services/catalog.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import DuplicateGroup, Track


@dataclass(frozen=True, slots=True)
class DuplicateTrackOut:
    id: int
    path: str
    title: str | None
    artist: str | None
    format: str | None
    bitrate: int | None
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class DuplicateGroupOut:
    id: int
    mb_recording_id: str
    basis: str
    dismissed: bool
    tracks: tuple[DuplicateTrackOut, ...]


def _to_track_out(t: Track) -> DuplicateTrackOut:
    return DuplicateTrackOut(
        id=t.id, path=t.path, title=t.title, artist=t.artist,
        format=t.format, bitrate=t.bitrate, duration_ms=t.duration_ms,
    )


def _to_group_out(g: DuplicateGroup) -> DuplicateGroupOut:
    tracks = tuple(
        _to_track_out(m.track) for m in g.members if m.track is not None
    )
    return DuplicateGroupOut(
        id=g.id, mb_recording_id=g.mb_recording_id, basis=g.basis,
        dismissed=g.dismissed, tracks=tracks,
    )


def list_duplicate_groups(
    session: Session, *, include_dismissed: bool = False
) -> list[DuplicateGroupOut]:
    stmt = select(DuplicateGroup).order_by(DuplicateGroup.created_at.desc())
    if not include_dismissed:
        stmt = stmt.where(DuplicateGroup.dismissed.is_(False))
    groups = session.scalars(stmt).all()
    return [_to_group_out(g) for g in groups]


def dismiss_duplicate_group(session: Session, group_id: int) -> DuplicateGroupOut:
    group = session.get(DuplicateGroup, group_id)
    if group is None:
        raise ValueError(f"duplicate group {group_id} not found")
    group.dismissed = True
    session.flush()
    return _to_group_out(group)


__all__ = [
    "DuplicateGroupOut",
    "DuplicateTrackOut",
    "dismiss_duplicate_group",
    "list_duplicate_groups",
]
