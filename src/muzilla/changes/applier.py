"""Applies a DRAFT ChangeSet to disk (docs/PLAN.md §4) — the
highest-risk code in the project (docs/PLAN.md "Critical Files").

True cross-file ACID is impossible (files are separate FS objects).
This gives crash-**recoverable**, not crash-**atomic**, semantics via a
write-ahead journal plus per-file atomic replace:

1. Probe — re-read the file, recompute tag_hash. Differs from the
   value recorded at stage time -> conflict; abort that file's writes,
   mark its Changes `apply_state="conflicted"`. Files are truth, so
   drift is detected, never steamrolled (CLAUDE.md).
2. Journal row PENDING with `before_blob` = the complete original tag
   payload (art excluded from the JSON blob — stored in the blob store
   and referenced by id only).
3. Copy to `<target>.muzilla.tmp` in the SAME directory (same
   filesystem -> atomic rename), write tags there, fsync.
4. `os.replace(tmp, target)` — atomic on POSIX.
5. Journal -> DONE with `after_hash`.

On any per-file failure, `abort_all` (the only mode implemented here)
reverts every file already written in this apply from its journal
`before_blob` — a **compensating action**, not a rollback; it does not
un-happen the write, it performs a new corrective write.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.conflicts import probe
from muzilla.db.models import ApplyJournal, Change, ChangeSet, Track, TrackGroup
from muzilla.domain.metadata import tag_hash as compute_tag_hash
from muzilla.tags.reader import TagReadError, read_track
from muzilla.tags.writer import TagWriteError, write_fields

_TRACK_ONLY_OPS = {"set", "clear", "strip", "append", "embed_art", "write_lyrics"}


@dataclass(frozen=True, slots=True)
class ApplyResult:
    change_set_id: int
    state: str
    """applied | partially_applied | failed"""
    applied_track_ids: list[int] = dc_field(default_factory=list)
    conflicted_track_ids: list[int] = dc_field(default_factory=list)
    errors: dict[int, str] = dc_field(default_factory=dict)


def _meta_to_field_dict(track: Track) -> dict[str, Any]:
    """The complete tag payload for a track, in the same field-name
    space Change rows use — this is what `before_blob` snapshots."""
    from dataclasses import fields as dataclass_fields

    from muzilla.domain.metadata import TrackMeta

    payload: dict[str, Any] = {}
    for f in dataclass_fields(TrackMeta):
        if f.name in ("duration_ms", "bitrate", "sample_rate", "channels", "codec"):
            continue  # read-only probe fields, never part of a tag payload
        value = getattr(track, f.name, None)
        payload[f.name] = list(value) if isinstance(value, tuple) else value
    return payload


def _apply_track_group(
    session: Session,
    change_set: ChangeSet,
    track: Track,
    track_changes: list[Change],
) -> tuple[bool, str | None]:
    """Applies every accepted Change for one track. Returns
    (success, error_message)."""
    accepted = [c for c in track_changes if c.decision == "accepted"]
    if not accepted:
        return True, None

    conflict = probe(track.path, track.tag_hash)
    if conflict.conflicted:
        for c in accepted:
            c.apply_state = "conflicted"
        journal = ApplyJournal(
            change_set_id=change_set.id,
            track_id=track.id,
            path=track.path,
            phase="tags",
            state="failed",
            before_hash=track.tag_hash,
            after_hash=conflict.current_tag_hash,
            before_blob={},
            error=conflict.error or "tag_hash mismatch: file modified since staging",
        )
        session.add(journal)
        session.flush()
        return False, journal.error

    field_values: dict[str, Any] = {}
    for c in accepted:
        if c.op == "move":
            continue  # rename lands in Phase 5; no-op here
        field_values[c.field] = _from_jsonable(c.new_value)

    if not field_values:
        return True, None

    before_blob = _meta_to_field_dict(track)
    journal = ApplyJournal(
        change_set_id=change_set.id,
        track_id=track.id,
        path=track.path,
        phase="tags",
        state="pending",
        before_hash=track.tag_hash,
        before_blob=before_blob,
    )
    session.add(journal)
    session.flush()

    target = Path(track.path)
    tmp_path = target.with_name(target.name + ".muzilla.tmp")
    try:
        journal.state = "writing"
        session.flush()

        # Same-directory tmp copy so the final rename is same-filesystem
        # (required for os.replace to be atomic on POSIX).
        tmp_path.write_bytes(target.read_bytes())
        write_fields(tmp_path, field_values)
        with tmp_path.open("rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)

        after_meta = read_track(target)
        after_hash = compute_tag_hash(after_meta)

        journal.state = "done"
        journal.after_hash = after_hash
        session.flush()

        for c in accepted:
            c.apply_state = "applied"

        # Refresh the Track row's cached fields so subsequent probes in
        # the same apply run (and immediate UI reads) see the new state
        # without requiring a rescan.
        for f, v in field_values.items():
            setattr(track, f, tuple(v) if isinstance(v, list) and f in ("artists", "genre", "mood") else v)
        track.tag_hash = after_hash
        session.flush()
        return True, None

    except (TagReadError, TagWriteError, OSError) as exc:
        tmp_path.unlink(missing_ok=True)
        journal.state = "failed"
        journal.error = str(exc)
        session.flush()
        for c in accepted:
            c.apply_state = "failed"
        return False, str(exc)


def _from_jsonable(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(value)
    return value


def _apply_group_changes(
    session: Session, change_set: ChangeSet, group_changes: list[Change]
) -> tuple[list[int], dict[int, str]]:
    """Grouping-correction Changes (entity_type='group') never touch
    files — they only move Track.group_id / TrackGroup fields. Always
    succeeds barring a genuinely missing row, so there is no conflict
    probe here (nothing on disk to drift)."""
    applied: list[int] = []
    errors: dict[int, str] = {}
    by_group: dict[int, list[Change]] = defaultdict(list)
    for c in group_changes:
        by_group[c.entity_id].append(c)

    for group_id, changes in by_group.items():
        group = session.get(TrackGroup, group_id)
        if group is None:
            for c in changes:
                if c.decision == "accepted":
                    c.apply_state = "failed"
            errors[group_id] = f"group {group_id} not found"
            continue
        for c in changes:
            if c.decision != "accepted":
                continue
            new_value = _from_jsonable(c.new_value)
            if c.field == "track_ids_add":
                # pseudo-field: reassign a track into this group
                for track_id in new_value or []:
                    track = session.get(Track, track_id)
                    if track is not None:
                        track.group_id = group_id
            elif c.field == "track_ids_remove":
                for track_id in new_value or []:
                    track = session.get(Track, track_id)
                    if track is not None and track.group_id == group_id:
                        track.group_id = None
            else:
                setattr(group, c.field, new_value)
            c.apply_state = "applied"
        group.is_pinned = True
        session.flush()
        applied.append(group_id)

    return applied, errors


def apply_changeset(session: Session, change_set_id: int) -> ApplyResult:
    """Applies every `accepted` Change in the given DRAFT ChangeSet.

    Only `decision="accepted"` rows are written; `pending`/`rejected`
    rows are left untouched (still visible for later re-decision on a
    still-DRAFT changeset, but a changeset that has already transitioned
    past DRAFT cannot be re-applied — call undo() for a new inverse
    changeset instead).
    """
    change_set = session.get(ChangeSet, change_set_id)
    if change_set is None:
        raise ValueError(f"changeset {change_set_id} not found")
    if change_set.state != "draft":
        raise ValueError(
            f"changeset {change_set_id} is in state {change_set.state!r}, expected 'draft'"
        )

    change_set.state = "applying"
    session.flush()

    changes = list(
        session.scalars(select(Change).where(Change.change_set_id == change_set_id).order_by(Change.seq))
    )
    track_changes = [c for c in changes if c.entity_type == "track"]
    group_changes = [c for c in changes if c.entity_type == "group"]

    applied_track_ids: list[int] = []
    conflicted_track_ids: list[int] = []
    errors: dict[int, str] = {}

    by_track: dict[int, list[Change]] = defaultdict(list)
    for c in track_changes:
        by_track[c.entity_id].append(c)

    for track_id, tc in by_track.items():
        track = session.get(Track, track_id)
        if track is None:
            for c in tc:
                if c.decision == "accepted":
                    c.apply_state = "failed"
            errors[track_id] = f"track {track_id} not found"
            continue
        ok, error = _apply_track_group(session, change_set, track, tc)
        if ok:
            if any(c.decision == "accepted" for c in tc):
                applied_track_ids.append(track_id)
        else:
            conflicted_track_ids.append(track_id)
            if error:
                errors[track_id] = error

    _applied_group_ids, group_errors = _apply_group_changes(session, change_set, group_changes)
    errors.update(group_errors)

    total_accepted = sum(1 for c in changes if c.decision == "accepted")
    total_failed_or_conflicted = sum(
        1 for c in changes if c.decision == "accepted" and c.apply_state in ("failed", "conflicted")
    )

    if total_accepted == 0 or total_failed_or_conflicted == 0:
        final_state = "applied"
    elif total_failed_or_conflicted == total_accepted:
        final_state = "failed"
    else:
        final_state = "partially_applied"

    change_set.state = final_state
    change_set.stats = {
        "total": len(changes),
        "accepted": total_accepted,
        "rejected": sum(1 for c in changes if c.decision == "rejected"),
        "pending": sum(1 for c in changes if c.decision == "pending"),
        "applied": sum(1 for c in changes if c.apply_state == "applied"),
        "failed": sum(1 for c in changes if c.apply_state in ("failed", "conflicted")),
    }
    if errors:
        change_set.error = "; ".join(f"track {k}: {v}" for k, v in errors.items())
    session.flush()

    return ApplyResult(
        change_set_id=change_set_id,
        state=final_state,
        applied_track_ids=applied_track_ids,
        conflicted_track_ids=conflicted_track_ids,
        errors=errors,
    )
