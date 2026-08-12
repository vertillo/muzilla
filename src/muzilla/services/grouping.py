"""Grouping service: runs the cascade and exposes the grouping
correction actions (merge/split/reassign/pin/force-to-singleton), each
staged as an ordinary ChangeSet — "a grouping correction is itself a
ChangeSet, so it is previewable and undoable like everything else — and then
immediately applied.

**Auto-apply, not stage-then-review** (the chosen behavior):
docs/completion-matrix.md tracked that none of these five actions
actually applied their changeset, so clicking "Pin" never flipped
Group.is_pinned and "Merge" never merged anything the API could see —
both silently no-op'd from the user's perspective. The chosen fix is
auto-apply, matching what the button labels already imply ("Pin" reads
as instant, not "stage a pin for later review"), rather than adding an
explicit apply step these five actions never had a UI affordance for
anyway. Every other changeset-producing action in this app (manual
edits, matches, renames) still stages-then-requires-review; this is a
deliberate exception scoped to grouping_correction only.

Applied via services.changesets.apply_now(), the same synchronous,
job-queue-bypassing entrypoint services/changesets.py's own docstring
already documents as intended for exactly this kind of internal caller
("tests and internal callers ... that want a synchronous result
without spinning up a worker"). Safe here specifically because a
grouping_correction changeset only ever touches TrackGroup/Track rows
in the same DB session (changes/applier.py's track_ids_add/
track_ids_remove/is_pinned pseudo-field handling) — no file moves, no
art blob writes — so apply_now's "no library_root/blob_store" limits
don't apply and this isn't a second write path alongside the job
queue's single-writer discipline, unlike a real changeset apply would be.

api/cli reach pipeline.grouping and changes.builder only through this
module (neither may import pipeline or changes directly).
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.db.models import ChangeSet, Track, TrackGroup
from muzilla.pipeline.grouping import GroupingRunResult, run_grouping_cascade
from muzilla.services.changesets import apply_now


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
    those need attention.

    Excludes empty groups (track_count == 0): a merge/split/reassign
    can leave behind a TrackGroup row with no tracks in it (the row
    itself is never deleted — the applier only ever moves Track.group_id
    pointers, per changes/applier.py's _apply_group_changes). Found
    while auto-apply made merge_groups' source
    group actually empty out for the first time; before that these
    changesets never applied, so an empty leftover group was never
    producible. Filtering here rather than deleting the row: deleting a
    TrackGroup is a separate, more invasive decision (anything else
    that might reference it by id, undo semantics for the emptying
    changeset) that this fix doesn't need to make — hiding an empty
    group from the workspace list is enough to make the list accurately
    reflect "groups you might need to act on."
    """
    stmt = select(TrackGroup).where(TrackGroup.track_count > 0)
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


def _build_and_apply(session: Session, **build_kwargs: object) -> ChangeSet:
    """Builds a grouping_correction ChangeSet and immediately applies
    it (see this module's docstring for why auto-apply is correct here
    and safe via apply_now specifically). `build_changeset` only adds
    the row to the session — a flush is needed first so it has an id
    apply_now's session.get() can find within the same session."""
    cs = build_changeset(session, **build_kwargs)  # type: ignore[arg-type]
    session.flush()
    apply_now(session, cs.id)
    session.refresh(cs)
    return cs


def merge_groups(
    session: Session, *, into_group_id: int, from_group_ids: list[int], created_by: str = "web"
) -> ChangeSet:
    """Moves every track from `from_group_ids` into `into_group_id`,
    pinning the destination so a rescan never re-guesses it apart
    again, and applies immediately."""
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
    return _build_and_apply(
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


def _get_or_create_singleton_group(session: Session, *, key: str) -> int:
    """Shared by split_group/force_to_singleton: a deterministically-
    keyed singleton TrackGroup, created on first use and reused if
    called again with the same key (e.g. re-splitting a track that was
    already force-split once before). session.flush() (not commit) so
    the new row has an id within the same in-progress changeset build."""
    existing = session.scalar(select(TrackGroup).where(TrackGroup.key == key))
    if existing is not None:
        return existing.id
    new_group = TrackGroup(
        key=key,
        kind="singleton",
        grouping_basis="manual",
        grouping_confidence=1.0,
        track_count=0,  # _apply_group_changes recomputes this once track_ids_add actually applies
    )
    session.add(new_group)
    session.flush()
    return new_group.id


def split_group(
    session: Session, *, group_id: int, track_ids: list[int], created_by: str = "web"
) -> ChangeSet:
    """Splits `track_ids` out of `group_id` into new singleton groups
    (one per track) — the simplest, always-safe split shape — and
    applies immediately. The user can subsequently merge the split-out
    tracks into a different group if they were meant to form a
    different album, itself another (also auto-applied) ChangeSet.

    Bug found while wiring auto-apply: this function's
    own docstring always claimed "into new singleton groups (one per
    track)", but the implementation only ever removed the tracks from
    the source group via track_ids_remove and never created the
    singleton groups it claimed to — every split track was left with
    group_id=None (ungrouped, not "in its own singleton group"), which
    was invisible as long as nothing applied these changesets at all.
    Fixed to match the documented behavior: one deterministically-keyed
    singleton group per split-out track, same pattern
    force_to_singleton() already used correctly."""
    if not track_ids:
        raise ValueError("no tracks selected to split")

    edits: dict[int, list[FieldEdit]] = {
        group_id: [
            FieldEdit(field="track_ids_remove", new_value=track_ids),
            _pin_edit(True),
        ]
    }
    for track_id in track_ids:
        key = blake2b(f"singleton-split:{track_id}".encode()).hexdigest()[:32]
        singleton_group_id = _get_or_create_singleton_group(session, key=key)
        edits.setdefault(singleton_group_id, []).extend(
            [FieldEdit(field="track_ids_add", new_value=[track_id]), _pin_edit(True)]
        )

    return _build_and_apply(
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
    """Drags a single track into a different (existing) group and
    applies immediately."""
    edits = {to_group_id: [FieldEdit(field="track_ids_add", new_value=[track_id]), _pin_edit(True)]}
    return _build_and_apply(
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
    album, and applies immediately."""
    track = session.get(Track, track_id)
    if track is None:
        raise ValueError(f"track {track_id} not found")

    key = blake2b(f"singleton-forced:{track_id}".encode()).hexdigest()[:32]
    group_id = _get_or_create_singleton_group(session, key=key)

    edits = {group_id: [FieldEdit(field="track_ids_add", new_value=[track_id]), _pin_edit(True)]}
    return _build_and_apply(
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
    never re-guess it — and applies immediately."""
    edits = {group_id: [_pin_edit(True)]}
    return _build_and_apply(
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
