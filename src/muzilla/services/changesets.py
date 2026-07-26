"""ChangeSet service: the only way api/cli create, inspect, decide, or
apply/undo ChangeSets (api/cli may not import muzilla.changes,
muzilla.jobs, or muzilla.db directly).

Returns plain dataclasses, never db.models rows — same boundary
discipline as services/catalog.py.

apply()/undo() enqueue a job and return immediately rather than
running inline — docs/PLAN.md §10's API spec is literally
`POST .../apply -> 202 {job_id}`, and running the highest-risk write
path (changes/applier.py) inline in a request handler was an
incidental second writer alongside the queue's single-writer
discipline. The actual apply_changeset/build_undo_changeset calls now
live in jobs/handlers/apply.py, run by the worker.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.changes.applier import ApplyResult, RecoveryReport, apply_changeset
from muzilla.changes.applier import recover_apply_journal as _recover_apply_journal
from muzilla.changes.differ import FieldDiff, diff_field
from muzilla.changes.undo import build_undo_changeset
from muzilla.db.models import Change, ChangeSet
from muzilla.jobs import queue

# Importing jobs/handlers/apply registers apply_changeset/undo_changeset
# (the @register decorator's side effect) — needed here since this
# module, not jobs/worker.py, is the entry point api/cli actually use.
from muzilla.jobs.handlers import apply as _apply_handler  # noqa: F401


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
class ChangeSetDetail(ChangeSetSummary):
    changes: tuple[ChangeOut, ...]


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
    the change is_manual — the in-review 'edit' action (docs/PLAN.md
    §9: "override any proposed value")."""


def _diff_for_change(change: Change) -> FieldDiff:
    return diff_field(
        change.field,
        _from_json(change.old_value),
        _from_json(change.new_value),
        op=change.op,
        old_blob_id=change.old_blob_id,
        new_blob_id=change.new_blob_id,
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


def _to_detail(cs: ChangeSet) -> ChangeSetDetail:
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
            diff=_diff_for_change(c),
        )
        for c in sorted(cs.changes, key=lambda c: c.seq)
    )
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
    stmt = select(ChangeSet)
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
    return _to_detail(cs) if cs is not None else None


def apply_decisions(
    session: Session, change_set_id: int, decisions: list[ChangeDecision]
) -> ChangeSetDetail:
    """PATCH /api/changesets/{id}/changes — bulk decisions + manual
    value edits, per docs/PLAN.md §10. Every accept/reject/edit persists
    immediately so closing the tab loses nothing."""
    cs = session.get(ChangeSet, change_set_id)
    if cs is None:
        raise ValueError(f"changeset {change_set_id} not found")
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


def apply(session: Session, change_set_id: int) -> int:
    """Enqueues an `apply_changeset` job and returns its id
    immediately — docs/PLAN.md §10: `POST .../apply -> 202 {job_id}`."""
    cs = session.get(ChangeSet, change_set_id)
    if cs is None:
        raise ValueError(f"changeset {change_set_id} not found")
    job = queue.enqueue(session, type="apply_changeset", payload={"change_set_id": change_set_id})
    return job.id


def undo(session: Session, change_set_id: int) -> int:
    """Enqueues an `undo_changeset` job and returns its id immediately.
    The resulting undo ChangeSet's id is in the job's `result` once it
    completes (`GET /api/jobs/{id}` or the SSE stream)."""
    cs = session.get(ChangeSet, change_set_id)
    if cs is None:
        raise ValueError(f"changeset {change_set_id} not found")
    job = queue.enqueue(session, type="undo_changeset", payload={"change_set_id": change_set_id})
    return job.id


def apply_now(session: Session, change_set_id: int) -> ApplyResult:
    """Runs apply_changeset directly, bypassing the job queue —
    intended for tests and internal callers (e.g. grouping corrections'
    own test suite) that want a synchronous result without spinning up
    a worker. Not used by api/cli, which always go through apply()
    to preserve single-writer discipline for a real running process.

    Deliberately does not accept library_root/create_directories: every
    known caller applies non-move changesets (tag edits, grouping
    corrections). apply_changeset's library-root guardrail is skipped
    entirely when library_root is None, so a caller that DID pass a
    rename ChangeSet here would get an unguarded move — if a future
    caller needs that, thread the params through rather than relying
    on this function's current callers never triggering it."""
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


def recover_apply_journal(session: Session) -> RecoveryReport:
    """Startup-only: api/app.py's lifespan and the CLI's `jobs worker`
    entrypoint call this (via this module, since neither may import
    muzilla.changes directly) before the worker pool starts, so no job
    can pick up a changeset whose journal is still mid-reconciliation."""
    return _recover_apply_journal(session)
