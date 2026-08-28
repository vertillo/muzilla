"""Grouping inference and constrained grouping corrections."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import Track, TrackGroup


def _pin_group(session: Session, group_id: int) -> None:
    group = session.get(TrackGroup, group_id)
    if group is not None:
        group.is_pinned = True
        session.flush()


def merge_groups(session: Session, *, into_group_id: int, from_group_ids: list[int], created_by: str = "web") -> int:
    if into_group_id in from_group_ids:
        raise ValueError("cannot merge a group into itself")
    dest = session.get(TrackGroup, into_group_id)
    if dest is None:
        raise ValueError(f"group {into_group_id} not found")
    for gid in from_group_ids:
        group = session.get(TrackGroup, gid)
        if group is None:
            continue
        for track in list(group.tracks):
            track.group_id = into_group_id
        session.delete(group)
    _pin_group(session, into_group_id)
    # recount
    dest.track_count = len(list(session.scalars(select(Track).where(Track.group_id == into_group_id))))
    session.flush()
    return into_group_id


def split_group(session: Session, *, group_id: int, track_ids: list[int], created_by: str = "web") -> int:
    if not track_ids:
        raise ValueError("no tracks selected to split")
    # simplified: create singleton groups for each track
    import hashlib
    for tid in track_ids:
        track = session.get(Track, tid)
        if track is None or track.group_id != group_id:
            continue
        key = hashlib.blake2b(f"split-singleton:{tid}".encode()).hexdigest()[:32]
        existing = session.scalar(select(TrackGroup).where(TrackGroup.key == key))
        if existing is None:
            group = TrackGroup(key=key, kind="singleton", grouping_basis="manual", is_pinned=True, track_count=1)
            session.add(group)
            session.flush()
            track.group_id = group.id
        else:
            track.group_id = existing.id
    _pin_group(session, group_id)
    group_opt = session.get(TrackGroup, group_id)
    if group_opt is not None:
        group_opt.track_count = len(list(session.scalars(select(Track).where(Track.group_id == group_id))))
    session.flush()
    return group_id


def reassign_track(session: Session, *, track_id: int, to_group_id: int, created_by: str = "web") -> int:
    track = session.get(Track, track_id)
    if track is None:
        raise ValueError(f"track {track_id} not found")
    dest = session.get(TrackGroup, to_group_id)
    if dest is None:
        raise ValueError(f"group {to_group_id} not found")
    track.group_id = to_group_id
    _pin_group(session, to_group_id)
    dest.track_count = len(list(session.scalars(select(Track).where(Track.group_id == to_group_id))))
    session.flush()
    return to_group_id


def force_to_singleton(session: Session, *, track_id: int, created_by: str = "web") -> int:
    import hashlib
    track = session.get(Track, track_id)
    if track is None:
        raise ValueError(f"track {track_id} not found")
    key = hashlib.blake2b(f"force-singleton:{track_id}".encode()).hexdigest()[:32]
    existing = session.scalar(select(TrackGroup).where(TrackGroup.key == key))
    if existing is None:
        group = TrackGroup(key=key, kind="singleton", grouping_basis="manual", is_pinned=True, track_count=1)
        session.add(group)
        session.flush()
        track.group_id = group.id
        session.flush()
        if track.group_id is None:
            raise ValueError("could not assign group")
        assert track.group_id is not None
        return track.group_id
    else:
        track.group_id = existing.id
        session.flush()
        if track.group_id is None:
            raise ValueError("could not assign group")
        assert track.group_id is not None
        return track.group_id


def pin_group(session: Session, *, group_id: int, created_by: str = "web") -> int:
    _pin_group(session, group_id)
    session.flush()
    return group_id
