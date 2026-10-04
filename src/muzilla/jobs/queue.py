"""SQLite-backed job queue primitives.

Not an in-memory queue: jobs must survive an API restart, so `pending` state lives
entirely in the `jobs` table and `lease_next` is how a worker claims
one.

Every function here takes a `Session` the caller already owns and
never opens its own — same shape as `changes/applier.py`. The worker
(`jobs/worker.py`) is the only caller that matters in practice, and it
follows the single-writer discipline documented in `db/engine.py`: one
short-lived session per unit of work, not one held for the worker's
lifetime.

Claims and owner-bound transitions use conditional SQL updates. SQLite's
writer serialization alone is not an exclusive claim: independent workers can
both select the same candidate before either update commits.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, or_, select, update  # pyright: ignore[reportMissingImports]
from sqlalchemy.exc import OperationalError  # pyright: ignore[reportMissingImports]
from sqlalchemy.orm import Session  # pyright: ignore[reportMissingImports]
from sqlalchemy.sql.elements import ColumnElement

from muzilla.db.models import (
    ApplyRun,
    Job,
    JobEvent,
    ReviewBundle,
    ReviewUndoRun,
    SystemState,
    TaskAttempt,
)
from muzilla.jobs.execution_lock import JobExecutionLock
from muzilla.pipeline.reviews import refresh_inbox_entry


class MaintenanceModeError(RuntimeError):
    """A persistent reset lock forbids new work."""


def _maintenance_active(session: Session) -> bool:
    state = session.get(SystemState, 1)
    return state is not None and state.maintenance_mode


def enqueue(
    session: Session,
    *,
    type: str,
    payload: dict[str, object],
    priority: int = 0,
    parent_job_id: int | None = None,
    commit: bool = True,
) -> Job:
    if _maintenance_active(session):
        raise MaintenanceModeError("Muzilla is resetting; new work is temporarily blocked")
    job = Job(type=type, payload=payload, priority=priority, parent_job_id=parent_job_id)
    session.add(job)
    if commit:
        session.commit()
    else:
        session.flush()
    return job


def _refresh_review_task_state(session: Session, bundle_id: int) -> None:
    bundle = session.get(ReviewBundle, bundle_id)
    if bundle is None or bundle.state not in {"preparing", "ready", "needs_attention"}:
        return
    attempts = list(
        session.scalars(
            select(TaskAttempt)
            .where(TaskAttempt.review_bundle_id == bundle_id)
            .order_by(TaskAttempt.kind, TaskAttempt.item_key, TaskAttempt.attempt_no)
        )
    )
    latest: dict[tuple[str, str], TaskAttempt] = {}
    for attempt in attempts:
        latest[(attempt.kind, attempt.item_key)] = attempt
    failures = [
        attempt
        for attempt in latest.values()
        if attempt.state in {"transient_failure", "permanent_failure"}
    ]
    if failures:
        bundle.state = "needs_attention"
        bundle.error = next(
            (attempt.error for attempt in failures if attempt.error),
            "optional task failed",
        )
    else:
        bundle.state = "ready"
        bundle.error = None
    refresh_inbox_entry(session, bundle_id)


def _finish_active_review_tasks(
    session: Session, job_id: int, *, state: str, error: str | None = None
) -> None:
    attempts = list(
        session.scalars(
            select(TaskAttempt).where(
                TaskAttempt.job_id == job_id,
                TaskAttempt.state.in_(("pending", "running")),
            )
        )
    )
    bundle_ids = {attempt.review_bundle_id for attempt in attempts}
    for attempt in attempts:
        attempt.state = state
        attempt.error = error
    session.flush()
    for bundle_id in bundle_ids:
        _refresh_review_task_state(session, bundle_id)


def next_pending_job_id(session: Session) -> int | None:
    """Returns the best pending candidate; callers must still claim by CAS."""
    return session.scalar(
        select(Job.id)
        .where(Job.state == "pending")
        .order_by(Job.priority.desc(), Job.created_at.asc())
        .limit(1)
    )


def lease_next(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int,
    candidate_id: int | None = None,
) -> Job | None:
    """Atomically claims a pending candidate, if it has not been claimed."""
    if _maintenance_active(session):
        return None
    candidate_id = candidate_id if candidate_id is not None else next_pending_job_id(session)
    if candidate_id is None:
        return None

    now = datetime.now(UTC)
    claimed = session.execute(
        update(Job)
        .where(Job.id == candidate_id, Job.state == "pending")
        .values(
            state="running",
            worker_id=worker_id,
            lease_until=now + timedelta(seconds=lease_seconds),
            attempts=Job.attempts + 1,
        )
        .returning(Job.id)
    ).scalar_one_or_none()
    if claimed is None:
        session.rollback()
        return None
    session.commit()
    session.expire_all()
    return session.get(Job, candidate_id)


def heartbeat(
    session: Session,
    job_id: int,
    *,
    worker_id: str,
    attempts: int,
    lease_seconds: int,
) -> bool:
    """Renews only the active lease held by this exact execution."""
    changed_id = session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.worker_id == worker_id,
            Job.attempts == attempts,
            Job.state.in_(("running", "cancelling")),
        )
        .values(lease_until=datetime.now(UTC) + timedelta(seconds=lease_seconds))
        .returning(Job.id)
    ).scalar_one_or_none()
    session.commit()
    return changed_id == job_id


def _append_state_event(session: Session, job_id: int, state: str) -> None:
    last_seq = session.scalar(
        select(JobEvent.seq).where(JobEvent.job_id == job_id).order_by(JobEvent.seq.desc())
    )
    session.add(
        JobEvent(job_id=job_id, seq=(last_seq or 0) + 1, kind="state", payload={"state": state})
    )


def _owner_filter(job_id: int, *, worker_id: str, attempts: int) -> tuple[ColumnElement[bool], ...]:
    return (
        Job.id == job_id,
        Job.worker_id == worker_id,
        Job.attempts == attempts,
        Job.state.in_(("running", "cancelling")),
    )


def _finish_transition(
    session: Session,
    job_id: int,
    state: str,
    *,
    result: dict[str, object] | None = None,
    error: str | None = None,
) -> None:
    job = session.get(Job, job_id)
    assert job is not None
    job.state = state
    job.worker_id = None
    job.lease_until = None
    if state == "succeeded":
        job.cancel_requested = False
    if result is not None:
        job.result = result
    if error is not None:
        job.error = error
    if state == "cancelled":
        _finish_active_review_tasks(session, job_id, state=state)
    elif state == "failed":
        _finish_active_review_tasks(session, job_id, state="transient_failure", error=error)
    else:
        _finish_active_review_tasks(
            session,
            job_id,
            state="permanent_failure",
            error="task job completed without reporting an outcome",
        )
    _append_state_event(session, job_id, state)
    session.commit()


def mark_succeeded(
    session: Session,
    job_id: int,
    result: dict[str, object],
    *,
    worker_id: str,
    attempts: int,
) -> bool:
    committed_operation = result.get("state") in {"applied", "undone"}
    owner = _owner_filter(job_id, worker_id=worker_id, attempts=attempts)
    statement = update(Job).where(*owner)
    if committed_operation:
        transitioned = session.execute(
            statement.values(
                state="succeeded",
                result=result,
                cancel_requested=False,
                worker_id=None,
                lease_until=None,
            ).returning(Job.id)
        ).scalar_one_or_none()
        state = "succeeded"
    else:
        transitioned = session.execute(
            statement.where(Job.state == "running", Job.cancel_requested.is_(False))
            .values(
                state="succeeded",
                result=result,
                worker_id=None,
                lease_until=None,
            )
            .returning(Job.id)
        ).scalar_one_or_none()
        state = "succeeded"
        if transitioned is None:
            transitioned = session.execute(
                update(Job)
                .where(
                    *owner,
                    (Job.state == "cancelling") | Job.cancel_requested.is_(True),
                )
                .values(state="cancelled", worker_id=None, lease_until=None)
                .returning(Job.id)
            ).scalar_one_or_none()
            state = "cancelled"
    if transitioned is None:
        session.rollback()
        return False
    _finish_transition(session, job_id, state, result=result if state == "succeeded" else None)
    return state == "succeeded"


def mark_failed(
    session: Session,
    job_id: int,
    error: str,
    *,
    worker_id: str,
    attempts: int,
    fail_even_if_cancel_requested: bool = False,
) -> bool:
    owner = _owner_filter(job_id, worker_id=worker_id, attempts=attempts)
    if fail_even_if_cancel_requested:
        failed = session.execute(
            update(Job)
            .where(*owner)
            .values(state="failed", error=error, worker_id=None, lease_until=None)
            .returning(Job.id)
        ).scalar_one_or_none()
    else:
        failed = session.execute(
            update(Job)
            .where(*owner, Job.state == "running", Job.cancel_requested.is_(False))
            .values(state="failed", error=error, worker_id=None, lease_until=None)
            .returning(Job.id)
        ).scalar_one_or_none()
    state = "failed"
    if failed is None and not fail_even_if_cancel_requested:
        failed = session.execute(
            update(Job)
            .where(*owner, (Job.state == "cancelling") | Job.cancel_requested.is_(True))
            .values(state="cancelled", worker_id=None, lease_until=None)
            .returning(Job.id)
        ).scalar_one_or_none()
        state = "cancelled"
    if failed is None:
        session.rollback()
        return False
    _finish_transition(session, job_id, state, error=error if state == "failed" else None)
    return True


def mark_cancelled(
    session: Session,
    job_id: int,
    result: dict[str, object] | None = None,
    *,
    worker_id: str,
    attempts: int,
) -> bool:
    transitioned = session.execute(
        update(Job)
        .where(*_owner_filter(job_id, worker_id=worker_id, attempts=attempts))
        .values(
            state="cancelled",
            result=result,
            worker_id=None,
            lease_until=None,
        )
        .returning(Job.id)
    ).scalar_one_or_none()
    if transitioned is None:
        session.rollback()
        return False
    _finish_transition(session, job_id, "cancelled", result=result)
    return True


def _is_busy_error(exc: OperationalError) -> bool:
    msg = str(exc).lower()
    return (
        ("database" + " is locked") in msg
        or ("database table" + " is locked") in msg
        or "busy" in msg
    )


def _commit_with_retry(session: Session, *, attempts: int = 5) -> None:
    """Commit with bounded retry on transient SQLite busy/locked errors."""
    for attempt in range(attempts):
        try:
            session.commit()
            return
        except OperationalError as exc:
            if not _is_busy_error(exc) or attempt == attempts - 1:
                raise
            session.rollback()
            time.sleep(0.05 * (2**attempt))
            continue


def request_cancel(session: Session, job_id: int) -> Job | None:
    """Atomically requests cancellation without overwriting a terminal result.

    Pending jobs become cancelled immediately. Running jobs retain their lease
    in ``cancelling`` until the execution acknowledges a safe checkpoint.
    """
    last_exc: Exception | None = None
    for attempt in range(5):
        try:
            target_state = session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state.in_(("pending", "running")))
                .values(
                    cancel_requested=True,
                    state=case((Job.state == "pending", "cancelled"), else_="cancelling"),
                )
                .returning(Job.state)
            ).scalar_one_or_none()
            if target_state is not None:
                if target_state == "cancelled":
                    _finish_active_review_tasks(session, job_id, state="cancelled")
                _append_state_event(session, job_id, target_state)
                session.commit()
            else:
                session.rollback()
            session.expire_all()
            return session.get(Job, job_id)
        except OperationalError as exc:
            last_exc = exc
            if not _is_busy_error(exc) or attempt == 4:
                raise
            session.rollback()
            time.sleep(0.05 * (2**attempt))
    if last_exc is not None:
        raise last_exc
    return None


def append_event(session: Session, job_id: int, kind: str, payload: dict[str, object]) -> JobEvent:
    """Assigns seq = max(existing seq for job_id) + 1. Not itself
    rate-limited — jobs/progress.py owns coalescing frequency."""
    for attempt in range(5):
        try:
            last_seq = session.scalar(
                select(JobEvent.seq).where(JobEvent.job_id == job_id).order_by(JobEvent.seq.desc())
            )
            event = JobEvent(job_id=job_id, seq=(last_seq or 0) + 1, kind=kind, payload=payload)
            session.add(event)
            session.commit()
            return event
        except OperationalError as exc:
            if not _is_busy_error(exc) or attempt == 4:
                raise
            session.rollback()
            time.sleep(0.05 * (2**attempt))
            continue
    # fallback - should be unreachable due to raise above
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
    include_system: bool = False,
) -> tuple[list[Job], str | None]:
    """Cursor-paginated by descending id (newest first) — same keyset
    pattern as services/changesets.py::list_changesets."""
    stmt = select(Job)
    if state is not None:
        stmt = stmt.where(Job.state == state)
    if not include_system:
        stmt = stmt.where(Job.type != "retention_sweep")
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


def _expired_lease_filter(job: Job, now: datetime) -> tuple[ColumnElement[bool], ...]:
    owner_clause = (
        Job.worker_id.is_(None) if job.worker_id is None else Job.worker_id == job.worker_id
    )
    return (
        Job.id == job.id,
        Job.state.in_(("running", "cancelling")),
        Job.attempts == job.attempts,
        owner_clause,
        (Job.state == "cancelling") | (Job.lease_until.is_(None)) | (Job.lease_until <= now),
    )


def _persisted_terminal_failure(result: object) -> bool:
    return (
        isinstance(result, dict)
        and result.get("state") == "failed"
        and isinstance(result.get("recovery_required"), bool)
    )


def _recovery_error(message: str, original: str | None) -> str:
    if original is None:
        return f"recovery_required: {message}"
    if "recovery_required" in original.lower():
        return original
    return f"recovery_required: {message}; prior error: {original}"


def _file_job_recovery(
    session: Session, job: Job
) -> tuple[str, dict[str, object] | None, str | None]:
    raw_id = job.payload.get("apply_run_id" if job.type == "apply_review_bundle" else "undo_run_id")
    if not isinstance(raw_id, int | str):
        error = "recovery_required: interrupted file job has no run id"
        return "failed", {"state": "failed", "recovery_required": True}, error
    try:
        run_id = int(raw_id)
    except (TypeError, ValueError):
        error = "recovery_required: interrupted file job has an invalid run id"
        return "failed", {"state": "failed", "recovery_required": True}, error

    if job.type == "apply_review_bundle":
        apply_run = session.get(ApplyRun, run_id)
        if apply_run is None:
            error = "recovery_required: interrupted ApplyRun is missing"
            return "failed", {"state": "failed", "recovery_required": True}, error
        if (
            apply_run.state == "applied"
            and isinstance(apply_run.result, dict)
            and apply_run.result.get("state") == "applied"
        ):
            result = {
                "apply_run_id": apply_run.id,
                "review_bundle_id": apply_run.review_bundle_id,
                **apply_run.result,
            }
            return "succeeded", result, None
        if apply_run.state == "failed" and _persisted_terminal_failure(apply_run.result):
            assert isinstance(apply_run.result, dict)
            recovery_required = bool(apply_run.result.get("recovery_required"))
            cancelled = bool(apply_run.result.get("cancelled")) or (
                apply_run.error is not None and apply_run.error.lower().startswith("cancelled")
            )
            target_state = "failed" if recovery_required or not cancelled else "cancelled"
            bundle = session.get(ReviewBundle, apply_run.review_bundle_id)
            if bundle is not None:
                refresh_inbox_entry(session, bundle.id)
            return (
                target_state,
                {
                    "apply_run_id": apply_run.id,
                    "review_bundle_id": apply_run.review_bundle_id,
                    **apply_run.result,
                },
                apply_run.error,
            )
        error = _recovery_error("interrupted Apply was not proven committed", apply_run.error)
        persisted_result = dict(apply_run.result) if isinstance(apply_run.result, dict) else {}
        persisted_result.update(
            state="failed",
            atomicity=persisted_result.get("atomicity", "review_bundle"),
            files=persisted_result.get("files", []),
            recovery_required=True,
        )
        apply_run.state = "failed"
        apply_run.error = error
        apply_run.result = persisted_result
        bundle = session.get(ReviewBundle, apply_run.review_bundle_id)
        if bundle is not None:
            if bundle.state == "applying":
                bundle.state = "failed"
            bundle.error = error
            refresh_inbox_entry(session, bundle.id)
        return (
            "failed",
            {
                "apply_run_id": apply_run.id,
                "review_bundle_id": apply_run.review_bundle_id,
                **persisted_result,
            },
            error,
        )

    undo_run = session.get(ReviewUndoRun, run_id)
    if undo_run is None:
        error = "recovery_required: interrupted ReviewUndoRun is missing"
        return "failed", {"state": "failed", "recovery_required": True}, error
    if (
        undo_run.state == "undone"
        and isinstance(undo_run.result, dict)
        and undo_run.result.get("state") == "undone"
    ):
        result = {
            "undo_run_id": undo_run.id,
            "review_bundle_id": undo_run.review_bundle_id,
            "source_apply_run_id": undo_run.source_apply_run_id,
            **undo_run.result,
        }
        return "succeeded", result, None
    if undo_run.state == "failed" and _persisted_terminal_failure(undo_run.result):
        assert isinstance(undo_run.result, dict)
        recovery_required = bool(undo_run.result.get("recovery_required"))
        cancelled = bool(undo_run.result.get("cancelled")) or (
            undo_run.error is not None and undo_run.error.lower().startswith("cancelled")
        )
        target_state = "failed" if recovery_required or not cancelled else "cancelled"
        bundle = session.get(ReviewBundle, undo_run.review_bundle_id)
        if bundle is not None:
            refresh_inbox_entry(session, bundle.id)
        return (
            target_state,
            {
                "undo_run_id": undo_run.id,
                "review_bundle_id": undo_run.review_bundle_id,
                "source_apply_run_id": undo_run.source_apply_run_id,
                **undo_run.result,
            },
            undo_run.error,
        )
    error = _recovery_error("interrupted Undo was not proven committed", undo_run.error)
    persisted_result = dict(undo_run.result) if isinstance(undo_run.result, dict) else {}
    persisted_result.update(
        state="failed",
        atomicity=persisted_result.get("atomicity", "review_bundle"),
        files=persisted_result.get("files", []),
        recovery_required=True,
    )
    undo_run.state = "failed"
    undo_run.error = error
    undo_run.result = persisted_result
    bundle = session.get(ReviewBundle, undo_run.review_bundle_id)
    if bundle is not None:
        bundle.error = error
        refresh_inbox_entry(session, bundle.id)
    return (
        "failed",
        {
            "undo_run_id": undo_run.id,
            "review_bundle_id": undo_run.review_bundle_id,
            "source_apply_run_id": undo_run.source_apply_run_id,
            **persisted_result,
        },
        error,
    )


def recover_stuck_jobs(session: Session) -> int:
    """Reconcile expired leases only after the OS proves no execution is live.

    A per-job POSIX lock is acquired nonblocking and held through the scoped
    CAS transition. Expired Apply/Undo work is never replayed or globally
    restored: a durable applied/undone run is recorded as success; uncertain
    work is persisted as recovery-required on both job and operation run.
    """
    now = datetime.now(UTC)
    candidate_ids = list(
        session.scalars(
            select(Job.id).where(
                or_(
                    Job.state == "cancelling",
                    and_(
                        Job.state == "running",
                        (Job.lease_until.is_(None)) | (Job.lease_until <= now),
                    ),
                )
            )
        )
    )
    session.commit()
    recovered = 0
    for job_id in candidate_ids:
        lock = JobExecutionLock.try_for_session(session, job_id)
        if lock is None:
            continue
        try:
            session.expire_all()
            job = session.get(Job, job_id)
            if job is None or job.state not in {"running", "cancelling"}:
                continue
            now = datetime.now(UTC)
            if (
                job.state != "cancelling"
                and job.lease_until is not None
                and _aware(job.lease_until) > now
            ):
                continue
            owner = _expired_lease_filter(job, now)
            cancelled = job.cancel_requested or job.state == "cancelling"
            result: dict[str, object] | None = None
            error: str | None = None
            if job.type in {"apply_review_bundle", "undo_review_bundle"}:
                target_state, result, error = _file_job_recovery(session, job)
            else:
                target_state = "cancelled" if cancelled else "pending"

            transitioned = session.execute(
                update(Job)
                .execution_options(synchronize_session=False)
                .where(*owner)
                .values(
                    state=target_state,
                    result=result if target_state != "pending" else Job.result,
                    error=error if error is not None else Job.error,
                    cancel_requested=False if target_state == "succeeded" else Job.cancel_requested,
                    worker_id=None,
                    lease_until=None,
                )
                .returning(Job.id)
            ).scalar_one_or_none()
            if transitioned is None:
                session.rollback()
                continue
            if target_state == "pending":
                attempts = list(
                    session.scalars(
                        select(TaskAttempt).where(
                            TaskAttempt.job_id == job_id,
                            TaskAttempt.state.in_(("pending", "running")),
                        )
                    )
                )
                for attempt in attempts:
                    attempt.state = "pending"
            elif target_state == "cancelled":
                _finish_active_review_tasks(session, job_id, state="cancelled")
            elif target_state == "failed":
                _finish_active_review_tasks(session, job_id, state="permanent_failure", error=error)
            _append_state_event(session, job_id, target_state)
            session.commit()
            recovered += 1
        finally:
            lock.close()
    return recovered
