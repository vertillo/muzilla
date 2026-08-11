"""Import session service: the only way api/cli start or inspect a
resumable whole-library import (docs/PLAN.md §7, Phase 4).

Starting an import never runs anything inline — it creates the
ImportSession/ImportTask rows and enqueues one Job(type='import'),
then returns immediately. The worker picks it up on its next poll.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import ChangeSet, ImportSession, ImportTask, ReviewBundle
from muzilla.jobs import queue

_STAGES = ("scan", "fingerprint", "group", "match")


@dataclass(frozen=True, slots=True)
class ImportTaskOut:
    stage: str
    seq: int
    state: str
    error: str | None


@dataclass(frozen=True, slots=True)
class ImportSessionSummary:
    id: int
    library_root: str
    state: str
    job_id: int | None
    stats: dict[str, object]
    error: str | None


@dataclass(frozen=True, slots=True)
class ImportSessionDetail(ImportSessionSummary):
    tasks: tuple[ImportTaskOut, ...]
    review_bundle_ids: tuple[int, ...]
    changeset_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ImportSessionPage:
    items: tuple[ImportSessionSummary, ...]


def _to_summary(session_row: ImportSession) -> ImportSessionSummary:
    return ImportSessionSummary(
        id=session_row.id,
        library_root=session_row.library_root,
        state=session_row.state,
        job_id=session_row.job_id,
        stats=dict(session_row.stats),
        error=session_row.error,
    )


def start_import(session: Session, library_root: str) -> ImportSessionSummary:
    import_session = ImportSession(library_root=library_root, stats={})
    session.add(import_session)
    session.flush()

    for i, stage in enumerate(_STAGES):
        session.add(ImportTask(import_session_id=import_session.id, stage=stage, seq=i))
    session.flush()

    job = queue.enqueue(
        session, type="import", payload={"import_session_id": import_session.id}
    )
    import_session.job_id = job.id
    session.commit()
    return _to_summary(import_session)


def list_import_sessions(session: Session, *, limit: int = 5) -> ImportSessionPage:
    """Small user-activity projection for the Dashboard, newest session first."""
    items = tuple(
        _to_summary(item)
        for item in session.scalars(
            select(ImportSession).order_by(ImportSession.id.desc()).limit(limit)
        )
    )
    return ImportSessionPage(items=items)


def get_import_session(session: Session, import_session_id: int) -> ImportSessionDetail | None:
    import_session = session.get(ImportSession, import_session_id)
    if import_session is None:
        return None

    tasks = tuple(
        ImportTaskOut(stage=t.stage, seq=t.seq, state=t.state, error=t.error)
        for t in sorted(import_session.tasks, key=lambda t: t.seq)
    )
    changeset_ids = tuple(
        cs.id
        for cs in session.query(ChangeSet)
        .filter(ChangeSet.import_session_id == import_session_id)
        .all()
    )
    review_bundle_ids = tuple(
        session.scalars(
            select(ReviewBundle.id)
            .where(ReviewBundle.import_session_id == import_session_id)
            .order_by(ReviewBundle.id)
        )
    )

    s = _to_summary(import_session)
    return ImportSessionDetail(
        id=s.id,
        library_root=s.library_root,
        state=s.state,
        job_id=s.job_id,
        stats=s.stats,
        error=s.error,
        tasks=tasks,
        review_bundle_ids=review_bundle_ids,
        changeset_ids=changeset_ids,
    )


def resume_import(session: Session, import_session_id: int) -> ImportSessionSummary:
    """Re-enqueues a fresh `import` job against the same session/tasks
    rows — `handle_import`'s per-task skip-if-done logic picks up
    wherever the previous run (or crash) left off."""
    import_session = session.get(ImportSession, import_session_id)
    if import_session is None:
        raise ValueError(f"import session {import_session_id} not found")

    job = queue.enqueue(
        session, type="import", payload={"import_session_id": import_session.id}
    )
    import_session.job_id = job.id
    import_session.error = None
    session.commit()
    return _to_summary(import_session)
