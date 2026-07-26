"""Grouping service: runs the cascade and exposes the grouping
correction actions (merge/split/reassign/pin), each staged as an
ordinary ChangeSet — "a grouping correction is itself a ChangeSet, so
it is previewable and undoable like everything else" (docs/PLAN.md
§7b).

api/cli reach pipeline.grouping and changes.builder only through this
module (neither may import pipeline or changes directly).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.db.models import ChangeSet, Track, TrackGroup
from muzilla.pipeline.grouping import GroupingRunResult, run_grouping_cascade


@dataclass(frozen=True, slots=True)
class GroupSummary:
    id: int
    key: str
    kind: str
    grouping_basis: str | None
    grouping_confidence: float | None
    is_pinned: bool
    album: str | None
    album_artist: str | None
    year: int | None
    track_count: int
    expected_track_count: int | None
    match_state: str


@dataclass(frozen=True, slots=True)
class GroupDetail(GroupSummary):
    track_ids: tuple[int, ...]


def _to_summary(g: TrackGroup) -> GroupSummary:
    return GroupSummary(
        id=g.id,
        key=g.key,
        kind=g.kind,
        grouping_basis=g.grouping_basis,
        grouping_confidence=g.grouping_confidence,
        is_pinned=g.is_pinned,
        album=g.album,
        album_artist=g.album_artist,
        year=g.year,
        track_count=g.track_count,
        expected_track_count=g.expected_track_count,
        match_state=g.match_state,
    )


def run_cascade(session: Session) -> GroupingRunResult:
    result = run_grouping_cascade(session)
    session.commit()
    return result


def list_groups(
    session: Session, *, sort: str = "confidence_asc", limit: int = 200
) -> list[GroupSummary]:
    """Sorted ascending by confidence by default — worst first, since
    those need attention (docs/PLAN.md §9 grouping workspace spec)."""
    stmt = select(TrackGroup)
    groups = list(session.scalars(stmt))
    if sort == "confidence_asc":
        groups.sort(key=lambda g: (g.grouping_confidence is None, g.grouping_confidence or 0.0))
    return [_to_summary(g) for g in groups[:limit]]


def get_group(session: Session, group_id: int) -> GroupDetail | None:
    g = session.get(TrackGroup, group_id)
    if g is None:
        return None
    track_ids = tuple(
        t.id for t in session.scalars(select(Track).where(Track.group_id == group_id))
    )
    s = _to_summary(g)
    return GroupDetail(
        id=s.id, key=s.key, kind=s.kind, grouping_basis=s.grouping_basis,
        grouping_confidence=s.grouping_confidence, is_pinned=s.is_pinned, album=s.album,
        album_artist=s.album_artist, year=s.year, track_count=s.track_count,
        expected_track_count=s.expected_track_count, match_state=s.match_state,
        track_ids=track_ids,
    )


def _pin_edit(pin: bool = True) -> FieldEdit:
    return FieldEdit(field="is_pinned", new_value=pin)


def merge_groups(
    session: Session, *, into_group_id: int, from_group_ids: list[int], created_by: str = "web"
) -> ChangeSet:
    """Moves every track from `from_group_ids` into `into_group_id`,
    pinning the destination so a rescan never re-guesses it apart
    again."""
    if into_group_id in from_group_ids:
        raise ValueError("cannot merge a group into itself")

    track_ids: list[int] = []
    for gid in from_group_ids:
        track_ids.extend(
            t.id for t in session.scalars(select(Track).where(Track.group_id == gid))
        )
    if not track_ids:
        raise ValueError("source groups have no tracks to merge")

    edits = {
        into_group_id: [
            FieldEdit(field="track_ids_add", new_value=track_ids),
            _pin_edit(True),
        ]
    }
    return build_changeset(
        session,
        title=f"Merge {len(from_group_ids)} group(s) into group {into_group_id}",
        source="grouping_correction",
        edits=edits,
        entity_type="group",
        scope_type="group",
        scope_id=into_group_id,
        source_ref={"action": "merge", "from_group_ids": ",".join(map(str, from_group_ids))},
        created_by=created_by,
    )


def split_group(
    session: Session, *, group_id: int, track_ids: list[int], created_by: str = "web"
) -> ChangeSet:
    """Splits `track_ids` out of `group_id` into new singleton groups
    (one per track) — the simplest, always-safe split shape. The user
    can subsequently merge the split-out tracks into a different group
    if they were meant to form a different album, itself another
    ChangeSet."""
    if not track_ids:
        raise ValueError("no tracks selected to split")

    edits = {
        group_id: [
            FieldEdit(field="track_ids_remove", new_value=track_ids),
            _pin_edit(True),
        ]
    }
    return build_changeset(
        session,
        title=f"Split {len(track_ids)} track(s) out of group {group_id}",
        source="grouping_correction",
        edits=edits,
        entity_type="group",
        scope_type="group",
        scope_id=group_id,
        source_ref={"action": "split", "track_ids": ",".join(map(str, track_ids))},
        created_by=created_by,
    )


def reassign_track(
    session: Session, *, track_id: int, to_group_id: int, created_by: str = "web"
) -> ChangeSet:
    """Drags a single track into a different (existing) group."""
    edits = {to_group_id: [FieldEdit(field="track_ids_add", new_value=[track_id]), _pin_edit(True)]}
    return build_changeset(
        session,
        title=f"Reassign track {track_id} to group {to_group_id}",
        source="grouping_correction",
        edits=edits,
        entity_type="group",
        scope_type="group",
        scope_id=to_group_id,
        source_ref={"action": "reassign", "track_id": str(track_id)},
        created_by=created_by,
    )


def force_to_singleton(session: Session, *, track_id: int, created_by: str = "web") -> ChangeSet:
    """Forces a track out of whatever group it's in and marks it (via a
    fresh, pinned singleton group) as deliberately not part of an
    album."""
    track = session.get(Track, track_id)
    if track is None:
        raise ValueError(f"track {track_id} not found")

    from hashlib import blake2b

    key = blake2b(f"singleton-forced:{track_id}".encode()).hexdigest()[:32]
    existing = session.scalar(select(TrackGroup).where(TrackGroup.key == key))
    if existing is None:
        new_group = TrackGroup(
            key=key,
            kind="singleton",
            grouping_basis="manual",
            grouping_confidence=1.0,
            track_count=1,
        )
        session.add(new_group)
        session.flush()
        group_id = new_group.id
    else:
        group_id = existing.id

    edits = {group_id: [FieldEdit(field="track_ids_add", new_value=[track_id]), _pin_edit(True)]}
    return build_changeset(
        session,
        title=f"Force track {track_id} to singleton",
        source="grouping_correction",
        edits=edits,
        entity_type="group",
        scope_type="group",
        scope_id=group_id,
        source_ref={"action": "force_singleton", "track_id": str(track_id)},
        created_by=created_by,
    )


def pin_group(session: Session, *, group_id: int, created_by: str = "web") -> ChangeSet:
    """Pins a group as-is with no other changes — the user reviewed an
    inferred grouping and confirmed it's correct, so rescans should
    never re-guess it."""
    edits = {group_id: [_pin_edit(True)]}
    return build_changeset(
        session,
        title=f"Pin group {group_id}",
        source="grouping_correction",
        edits=edits,
        entity_type="group",
        scope_type="group",
        scope_id=group_id,
        source_ref={"action": "pin"},
        created_by=created_by,
    )
