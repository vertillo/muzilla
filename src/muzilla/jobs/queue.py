"""SQLite-backed job queue primitives.

Not an in-memory queue: jobs must survive an API restart (docs/PLAN.md
§4, Phase 4's own crash-recovery requirement), so `pending` state lives
entirely in the `jobs` table and `lease_next` is how a worker claims
one.

Every function here takes a `Session` the caller already owns and
never opens its own — same shape as `changes/applier.py`. The worker
(`jobs/worker.py`) is the only caller that matters in practice, and it
follows the single-writer discipline documented in `db/engine.py`: one
short-lived session per unit of work, not one held for the worker's
lifetime.

Safe without `SELECT ... FOR UPDATE` (SQLite doesn't have real
row-level locking anyway): only one worker session ever writes to
`jobs` at a time, because `lease_next`'s SELECT-then-UPDATE happens
inside one transaction on one connection, and SQLite's WAL mode
serializes writers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import Job, JobEvent


def enqueue(
    session: Session,
    *,
    type: str,
    payload: dict[str, object],
    priority: int = 0,
    parent_job_id: int | None = None,
) -> Job:
    job = Job(type=type, payload=payload, priority=priority, parent_job_id=parent_job_id)
    session.add(job)
    session.commit()
    return job


def lease_next(session: Session, *, worker_id: str, lease_seconds: int) -> Job | None:
    """Claims the highest-priority, oldest pending job, if any.

    SELECT + UPDATE in one transaction on the caller's session — no
    separate locking needed since single-writer discipline means no
    other session is doing this concurrently.
    """
    stmt = (
        select(Job)
        .where(Job.state == "pending")
        .order_by(Job.priority.desc(), Job.created_at.asc())
        .limit(1)
    )
    job = session.scalars(stmt).first()
    if job is None:
        return None

    now = datetime.now(UTC)
    job.state = "running"
    job.worker_id = worker_id
    job.lease_until = now + timedelta(seconds=lease_seconds)
    job.attempts += 1
    session.commit()
    return job


def heartbeat(session: Session, job_id: int, *, lease_seconds: int) -> None:
    """Renews a running job's lease so a long-running stage isn't
    reclaimed by startup crash recovery while it's still alive."""
    job = session.get(Job, job_id)
    if job is None:
        return
    job.lease_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
    session.commit()


def mark_succeeded(session: Session, job_id: int, result: dict[str, object]) -> None:
    job = session.get(Job, job_id)
    if job is None:
        return
    job.state = "succeeded"
    job.result = result
    session.commit()


def mark_failed(session: Session, job_id: int, error: str) -> None:
    job = session.get(Job, job_id)
    if job is None:
        return
    job.state = "failed"
    job.error = error
    session.commit()


def mark_cancelled(session: Session, job_id: int) -> None:
    job = session.get(Job, job_id)
    if job is None:
        return
    job.state = "cancelled"
    session.commit()


def request_cancel(session: Session, job_id: int) -> None:
    """Sets `cancel_requested` only. The queue never force-kills a
    running handler — the handler must observe this flag between
    pipeline stages and exit cooperatively (see jobs/worker.py)."""
    job = session.get(Job, job_id)
    if job is None:
        return
    job.cancel_requested = True
    session.commit()


def append_event(
    session: Session, job_id: int, kind: str, payload: dict[str, object]
) -> JobEvent:
    """Assigns seq = max(existing seq for job_id) + 1. Not itself
    rate-limited — jobs/progress.py owns coalescing frequency."""
    last_seq = session.scalar(
        select(JobEvent.seq).where(JobEvent.job_id == job_id).order_by(JobEvent.seq.desc())
    )
    event = JobEvent(job_id=job_id, seq=(last_seq or 0) + 1, kind=kind, payload=payload)
    session.add(event)
    session.commit()
    return event


def get_job(session: Session, job_id: int) -> Job | None:
    return session.get(Job, job_id)


def list_jobs(
    session: Session,
    *,
    state: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> tuple[list[Job], str | None]:
    """Cursor-paginated by descending id (newest first) — same keyset
    pattern as services/changesets.py::list_changesets."""
    stmt = select(Job)
    if state is not None:
        stmt = stmt.where(Job.state == state)
    if cursor is not None:
        stmt = stmt.where(Job.id < int(cursor))
    stmt = stmt.order_by(Job.id.desc()).limit(limit + 1)

    rows = list(session.scalars(stmt))
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None
    return items, next_cursor


def list_events_after(session: Session, job_id: int, after_seq: int) -> list[JobEvent]:
    stmt = (
        select(JobEvent)
        .where(JobEvent.job_id == job_id, JobEvent.seq > after_seq)
        .order_by(JobEvent.seq.asc())
    )
    return list(session.scalars(stmt))


def _aware(value: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; treat naive values as UTC
    (everything here is written via datetime.now(UTC))."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def recover_stuck_jobs(session: Session) -> int:
    """Startup-only: resets any job left `running` with an expired (or
    missing) lease — the trace of a worker that died before clean
    shutdown. Never called from inside the poll loop, only once at
    process start, since a live worker renews its own lease."""
    now = datetime.now(UTC)
    stmt = select(Job).where(Job.state == "running")
    stuck = [
        job
        for job in session.scalars(stmt)
        if job.lease_until is None or _aware(job.lease_until) <= now
    ]
    for job in stuck:
        job.state = "pending"
        job.worker_id = None
        job.lease_until = None
    if stuck:
        session.commit()
    return len(stuck)
