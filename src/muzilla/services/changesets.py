"""ChangeSet service: the only way api/cli create, inspect, decide, or
apply/undo ChangeSets (api/cli may not import muzilla.changes,
muzilla.jobs, or muzilla.db directly).

Returns plain dataclasses, never db.models rows — same boundary
discipline as services/catalog.py.

apply()/undo() enqueue a job and return immediately rather than
running inline — docs/product-spec.md API spec is literally
`POST .../apply -> 202 {job_id}`, and running the highest-risk write
path (changes/applier.py) inline in a request handler was an
incidental second writer alongside the queue's single-writer
discipline. The actual apply_changeset/build_undo_changeset calls now
live in jobs/handlers/apply.py, run by the worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.changes.applier import ApplyResult, RecoveryReport, apply_changeset
from muzilla.changes.applier import recover_apply_journal as _recover_apply_journal
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.differ import FieldDiff, diff_field
from muzilla.changes.undo import (
    FROZEN_REVIEW_UNDO_CREATED_BY,
    build_undo_changeset,
)
from muzilla.db.models import Blob, Change, ChangeSet, Track, TrackGroup
from muzilla.jobs import queue

# Importing jobs/handlers/apply registers apply_changeset/undo_changeset
# (the @register decorator's side effect) — needed here since this
# module, not jobs/worker.py, is the entry point api/cli actually use.
from muzilla.jobs.handlers import apply as _apply_handler  # noqa: F401


class ChangeSetNotFoundError(ValueError):
    """The legacy ChangeSet API must not expose an internal frozen inverse."""


def _is_frozen_review_undo(change_set: ChangeSet) -> bool:
    return change_set.created_by == FROZEN_REVIEW_UNDO_CREATED_BY


def _legacy_mutation_target(session: Session, change_set_id: int) -> ChangeSet:
    change_set = session.get(ChangeSet, change_set_id)
    if change_set is None:
        raise ValueError(f"changeset {change_set_id} not found")
    if _is_frozen_review_undo(change_set):
        raise ChangeSetNotFoundError("changeset not found")
    return change_set


@dataclass(frozen=True, slots=True)
class ChangeOut:
    id: int
    seq: int
    entity_type: str
    entity_id: int
    field: str
    op: str
    old_value: object
    new_value: object
    confidence: float | None
    severity: str
    decision: str
    apply_state: str
    is_manual: bool
    diff: FieldDiff


@dataclass(frozen=True, slots=True)
class ChangeSetSummary:
    id: int
    title: str
    source: str
    state: str
    scope_type: str
    scope_id: int | None
    created_by: str
    candidate_source: str | None
    candidate_ref: str | None
    undo_of_id: int | None
    stats: dict[str, int]
    error: str | None


@dataclass(frozen=True, slots=True)
class ChangeSetEntity:
    entity_type: str
    """track | group"""
    entity_id: int
    label: str
    """album mode: "3. Svefn-g-englar" (track_no, then title)
    singleton/group: "Sigur Ros, Svefn-g-englar" (artist, then title/album)"""
    sort_key: int | None
    """Track number where known, else None — entities with no sort_key
    sort after those with one, then by label."""


@dataclass(frozen=True, slots=True)
class ChangeSetDetail(ChangeSetSummary):
    changes: tuple[ChangeOut, ...]
    entities: tuple[ChangeSetEntity, ...]


@dataclass(frozen=True, slots=True)
class ChangeSetPage:
    items: tuple[ChangeSetSummary, ...]
    next_cursor: str | None
    total: int


@dataclass(frozen=True, slots=True)
class ChangeDecision:
    change_id: int
    decision: str
    """pending | accepted | rejected"""
    new_value: object | None = None
    """If provided alongside decision, overrides new_value and marks
    the change is_manual — the in-review 'edit' action (docs/product-spec.md
    §9: "override any proposed value")."""


def _blob_summary(session: Session, blob_id: int | None) -> str | None:
    if blob_id is None:
        return None
    blob = session.get(Blob, blob_id)
    if blob is None:
        return None
    size_kb = blob.size / 1024
    dims = f"{blob.width}x{blob.height} " if blob.width and blob.height else ""
    return f"{dims}{blob.mime} {size_kb:.1f}KB"


def _diff_for_change(session: Session, change: Change) -> FieldDiff:
    return diff_field(
        change.field,
        _from_json(change.old_value),
        _from_json(change.new_value),
        op=change.op,
        old_blob_id=change.old_blob_id,
        new_blob_id=change.new_blob_id,
        old_binary_summary=_blob_summary(session, change.old_blob_id),
        new_binary_summary=_blob_summary(session, change.new_blob_id),
    )


def _from_json(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


def _to_summary(cs: ChangeSet) -> ChangeSetSummary:
    return ChangeSetSummary(
        id=cs.id,
        title=cs.title,
        source=cs.source,
        state=cs.state,
        scope_type=cs.scope_type,
        scope_id=cs.scope_id,
        created_by=cs.created_by,
        candidate_source=cs.candidate_source,
        candidate_ref=cs.candidate_ref,
        undo_of_id=cs.undo_of_id,
        stats=dict(cs.stats),
        error=cs.error,
    )


def _track_label(track: Track, group_by_id: dict[int, TrackGroup]) -> tuple[str, int | None]:
    """(label, sort_key) for one track. Album mode (the track's group
    has more than one track) labels by position: "3. Svefn-g-englar".
    Singleton/ungrouped tracks label by artist and title, since a bare
    track number means nothing outside an album context. The en dash
    below is deliberate UI punctuation, not a typo (RUF001 noqa)."""
    group = group_by_id.get(track.group_id) if track.group_id is not None else None
    title = track.title or track.filename
    if group is not None and group.track_count > 1:
        prefix = f"{track.track_no}. " if track.track_no is not None else ""
        return f"{prefix}{title}", track.track_no
    artist = track.artist or "Unknown artist"
    return f"{artist} – {title}", track.track_no  # noqa: RUF001


def _group_label(group: TrackGroup) -> str:
    artist = group.album_artist or "Unknown artist"
    album = group.album or "(untitled)"
    return f"{artist} – {album}"  # noqa: RUF001


def _build_entities(session: Session, changes: tuple[Change, ...]) -> tuple[ChangeSetEntity, ...]:
    """One batched query per entity_type — never one query per entity
    (CLAUDE.md and §11g both call out unbatched IN() sites here as a
    recurring defect)."""
    pairs = {(c.entity_type, c.entity_id) for c in changes}
    track_ids = {eid for etype, eid in pairs if etype == "track"}
    group_ids = {eid for etype, eid in pairs if etype == "group"}

    tracks_by_id: dict[int, Track] = {}
    referenced_group_ids: set[int] = set(group_ids)
    if track_ids:
        tracks = list(session.scalars(select(Track).where(Track.id.in_(track_ids))))
        tracks_by_id = {t.id: t for t in tracks}
        referenced_group_ids |= {t.group_id for t in tracks if t.group_id is not None}

    groups_by_id: dict[int, TrackGroup] = {}
    if referenced_group_ids:
        groups = list(session.scalars(select(TrackGroup).where(TrackGroup.id.in_(referenced_group_ids))))
        groups_by_id = {g.id: g for g in groups}

    entities: list[ChangeSetEntity] = []
    for entity_type, entity_id in pairs:
        if entity_type == "track":
            track = tracks_by_id.get(entity_id)
            if track is None:
                continue
            label, sort_key = _track_label(track, groups_by_id)
        elif entity_type == "group":
            group = groups_by_id.get(entity_id)
            if group is None:
                continue
            label, sort_key = _group_label(group), None
        else:
            continue
        entities.append(ChangeSetEntity(entity_type=entity_type, entity_id=entity_id, label=label, sort_key=sort_key))

    entities.sort(key=lambda e: (e.sort_key is None, e.sort_key or 0, e.label))
    return tuple(entities)


def _to_detail(session: Session, cs: ChangeSet) -> ChangeSetDetail:
    changes = tuple(
        ChangeOut(
            id=c.id,
            seq=c.seq,
            entity_type=c.entity_type,
            entity_id=c.entity_id,
            field=c.field,
            op=c.op,
            old_value=c.old_value,
            new_value=c.new_value,
            confidence=c.confidence,
            severity=c.severity,
            decision=c.decision,
            apply_state=c.apply_state,
            is_manual=c.is_manual,
            diff=_diff_for_change(session, c),
        )
        for c in sorted(cs.changes, key=lambda c: c.seq)
    )
    entities = _build_entities(session, tuple(cs.changes))
    s = _to_summary(cs)
    return ChangeSetDetail(
        id=s.id,
        title=s.title,
        source=s.source,
        state=s.state,
        scope_type=s.scope_type,
        scope_id=s.scope_id,
        created_by=s.created_by,
        candidate_source=s.candidate_source,
        candidate_ref=s.candidate_ref,
        undo_of_id=s.undo_of_id,
        stats=s.stats,
        error=s.error,
        changes=changes,
        entities=entities,
    )


def list_changesets(
    session: Session,
    *,
    state: str | None = None,
    limit: int = 100,
    cursor: str | None = None,
) -> ChangeSetPage:
    """Cursor-paginated by descending id (newest first) — simple keyset
    over the primary key, since change_sets has no natural sort column
    users configure like tracks does."""
    stmt = select(ChangeSet).where(
        ChangeSet.created_by != FROZEN_REVIEW_UNDO_CREATED_BY
    )
    if state:
        stmt = stmt.where(ChangeSet.state == state)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = session.scalar(count_stmt) or 0

    if cursor is not None:
        last_id = int(cursor)
        stmt = stmt.where(ChangeSet.id < last_id)

    stmt = stmt.order_by(ChangeSet.id.desc()).limit(limit + 1)
    rows = list(session.scalars(stmt))
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None

    return ChangeSetPage(
        items=tuple(_to_summary(cs) for cs in items), next_cursor=next_cursor, total=total
    )


def get_changeset(session: Session, change_set_id: int) -> ChangeSetDetail | None:
    cs = session.get(ChangeSet, change_set_id)
    if cs is None or _is_frozen_review_undo(cs):
        return None
    return _to_detail(session, cs)


def apply_decisions(
    session: Session, change_set_id: int, decisions: list[ChangeDecision]
) -> ChangeSetDetail:
    """PATCH /api/changesets/{id}/changes — bulk decisions + manual
    value edits, per docs/product-spec.md Every accept/reject/edit persists
    immediately so closing the tab loses nothing."""
    cs = _legacy_mutation_target(session, change_set_id)
    if cs.state != "draft":
        raise ValueError(f"changeset {change_set_id} is not draft (state={cs.state!r})")

    by_id = {c.id: c for c in cs.changes}
    for decision in decisions:
        change = by_id.get(decision.change_id)
        if change is None:
            raise ValueError(f"change {decision.change_id} not in changeset {change_set_id}")
        if decision.decision not in ("pending", "accepted", "rejected"):
            raise ValueError(f"invalid decision: {decision.decision!r}")
        change.decision = decision.decision
        if decision.new_value is not None:
            change.new_value = decision.new_value
            change.is_manual = True

    counts = {"total": 0, "accepted": 0, "rejected": 0, "pending": 0}
    for c in cs.changes:
        counts["total"] += 1
        counts[c.decision] = counts.get(c.decision, 0) + 1
    cs.stats = counts
    session.commit()
    detail = get_changeset(session, change_set_id)
    assert detail is not None
    return detail


def apply(session: Session, change_set_id: int, *, backup: bool | None = None) -> int:
    """Enqueues an `apply_changeset` job and returns its id
    immediately — docs/product-spec.md: `POST .../apply -> 202 {job_id}`.

    `backup` (docs/product-spec.md) is passed through to the job payload
    as-is; `None` means "use the configured apply.backup default,"
    decided by the job handler (which has the Config), not here."""
    cs = _legacy_mutation_target(session, change_set_id)
    if cs.state != "draft":
        raise ValueError(f"changeset {change_set_id} is not draft (state={cs.state!r})")
    if not any(change.decision == "accepted" for change in cs.changes):
        raise ValueError(f"changeset {change_set_id} has no accepted changes")
    payload: dict[str, object] = {"change_set_id": change_set_id}
    if backup is not None:
        payload["backup"] = backup
    job = queue.enqueue(session, type="apply_changeset", payload=payload)
    return job.id


def undo(session: Session, change_set_id: int) -> int:
    """Enqueues an `undo_changeset` job and returns its id immediately.
    The resulting undo ChangeSet's id is in the job's `result` once it
    completes (`GET /api/jobs/{id}` or the SSE stream)."""
    _legacy_mutation_target(session, change_set_id)
    job = queue.enqueue(session, type="undo_changeset", payload={"change_set_id": change_set_id})
    return job.id


def apply_now(session: Session, change_set_id: int) -> ApplyResult:
    """Runs apply_changeset directly, bypassing the job queue —
    intended for tests and internal callers that want a synchronous
    result without spinning up a worker, and for changeset sources that
    are known never to touch the filesystem (see the parameter caveat
    below). Every other real-write changeset (tag edits, renames,
    matches) still goes through apply()'s job-queue path to preserve
    single-writer discipline for a real running process — this is the
    deliberate exception, not the general rule.

    services/grouping.py's five grouping_correction actions
    (pin/merge/split/reassign/force-to-singleton) are the one api-facing
    caller (Phase 7 item 6, docs/completion-matrix.md's fix — auto-apply is
    the chosen product behavior for those five specifically): safe here
    because a grouping_correction changeset only ever mutates TrackGroup/
    Track rows in the same DB session (changes/applier.py's
    track_ids_add/track_ids_remove/is_pinned pseudo-field handling) — no
    file moves, no blob writes — so this isn't a second write path
    alongside the job queue's single-writer discipline the way calling
    this for a real tag/rename changeset would be.

    Deliberately does not accept library_root/create_directories, or a
    blob_store: every known caller applies non-move, non-embed_art
    changesets (tag edits, grouping corrections). apply_changeset's
    library-root guardrail is skipped entirely when library_root is
    None (a caller that DID pass a rename ChangeSet here would get an
    unguarded move), and an embed_art Change fails outright with no
    blob_store — if a future caller needs either, thread the params
    through rather than relying on this function's current callers
    never triggering them."""
    result = apply_changeset(session, change_set_id)
    session.commit()
    return result


def undo_now(session: Session, change_set_id: int) -> ChangeSetDetail:
    """Synchronous equivalent of undo() — see apply_now()'s docstring."""
    undo_cs = build_undo_changeset(session, change_set_id)
    session.commit()
    detail = get_changeset(session, undo_cs.id)
    assert detail is not None
    return detail


def recover_apply_journal(
    session: Session, *, blob_dir: Path | None = None
) -> RecoveryReport:
    """Startup-only: api/app.py's lifespan and the CLI's `jobs worker`
    entrypoint call this (via this module, since neither may import
    muzilla.changes directly) before the worker pool starts, so no job
    can pick up a changeset whose journal is still mid-reconciliation."""
    return _recover_apply_journal(
        session,
        blob_store=BlobStore(blob_dir) if blob_dir is not None else None,
    )
