"""Application service for persistent ReviewBundle undo enqueue and retry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

# build_undo removed
# legacy undo removed
from muzilla.db.models import ApplyRun, Job, ReviewBundle, ReviewUndoRun, Track
from muzilla.jobs import queue

# Register the worker handler when this service is the API entry point.
from muzilla.jobs.handlers import apply as _apply_handler  # noqa: F401


class ReviewUndoError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReviewUndoEnqueued:
    undo_run_id: int
    job_id: int


def _job_ids(run: ReviewUndoRun) -> list[int]:
    raw = run.manifest.get("job_ids", [])
    if not isinstance(raw, list):
        return []
    return [value for value in raw if isinstance(value, int)]


def _active_or_completed_job(session: Session, run: ReviewUndoRun) -> Job | None:
    ids = _job_ids(run)
    if not ids:
        return None
    jobs = {
        job.id: job for job in session.scalars(select(Job).where(Job.id.in_(ids)))
    }
    for job_id in reversed(ids):
        job = jobs.get(job_id)
        if job is not None and job.state in {"pending", "running", "cancelling"}:
            return job
    if run.state == "undone":
        return next((jobs[job_id] for job_id in reversed(ids) if job_id in jobs), None)
    return None


def _track_checkpoint(track: Track) -> dict[str, object]:
    if track.tag_hash is None:
        raise ReviewUndoError("catalog has no current tag hash for undo")
    return {
        "path": track.path,
        "size_bytes": track.size_bytes,
        "mtime_ns": track.mtime_ns,
        "tag_hash": track.tag_hash,
    }


def _manifest_for_inverses(session: Session, source_run: ApplyRun, inverses: tuple[object, ...]) -> dict[str, object]:
    # Minimal manifest without ChangeSet: use apply_run's operation_attempts to derive files
    # For ponytail, we create one file entry per track that had applied operations
    from sqlalchemy import select as _select

    from muzilla.db.models import Operation, OperationAttempt
    files: list[dict[str, object]] = []
    # Derive track_ids from operation attempts that succeeded
    attempts = list(session.scalars(_select(OperationAttempt).where(OperationAttempt.apply_run_id == source_run.id)))
    # Group by track via operation target
    track_ids_set = set()
    for att in attempts:
        if att.state == "applied":
            op = session.get(Operation, att.operation_id)
            if op is not None and op.target_type == "track":
                track_ids_set.add(op.target_id)
    for track_id in sorted(track_ids_set):
        track = session.get(Track, track_id)
        if track is None:
            continue
        files.append({"track_id": track_id, "source": _track_checkpoint(track), "state": "pending", "error": None, "retryable": True, "steps": []})
    if not files:
        raise ReviewUndoError("apply run has no successful file operations to undo")
    return {"version": 1, "source_apply_run_id": source_run.id, "files": files, "job_ids": []}


def _create_run(
    session: Session,
    bundle: ReviewBundle,
    source_run: ApplyRun,
    *,
    idempotency_key: str,
) -> ReviewUndoRun:
    now = datetime.now(UTC)
    inserted_id = session.scalar(
        sqlite_insert(ReviewUndoRun)
        .values(
            review_bundle_id=bundle.id,
            source_apply_run_id=source_run.id,
            idempotency_key=idempotency_key,
            state="pending",
            manifest={},
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing()
        .returning(ReviewUndoRun.id)
    )
    if inserted_id is None:
        same_request = session.scalar(
            select(ReviewUndoRun).where(
                ReviewUndoRun.review_bundle_id == bundle.id,
                ReviewUndoRun.idempotency_key == idempotency_key,
            )
        )
        if same_request is not None:
            return same_request
        existing = session.scalar(
            select(ReviewUndoRun).where(
                ReviewUndoRun.source_apply_run_id == source_run.id
            )
        )
        if existing is not None:
            return existing
        raise ReviewUndoError("review already has an active undo run")
    run = session.get(ReviewUndoRun, inserted_id)
    if run is None:  # pragma: no cover - inserted in this transaction
        raise ReviewUndoError("could not create undo run")
    inverses: tuple[object, ...] = ()
    run.manifest = _manifest_for_inverses(session, source_run, inverses)
    session.flush()
    return run


def enqueue_review_undo(
    session: Session,
    review_bundle_id: int,
    *,
    apply_run_id: int,
    idempotency_key: str,
    backup: bool | None = None,
) -> ReviewUndoEnqueued:
    if not idempotency_key.strip():
        raise ReviewUndoError("idempotency key must not be empty")
    bundle = session.get(ReviewBundle, review_bundle_id)
    if bundle is None:
        raise ReviewUndoError(f"review bundle {review_bundle_id} not found")
    source_run = session.get(ApplyRun, apply_run_id)
    if source_run is None or source_run.review_bundle_id != review_bundle_id:
        raise ReviewUndoError("apply run does not belong to this review")
    if source_run.state not in {"applied", "partially_applied"}:
        raise ReviewUndoError("only an applied or partially applied run can be undone")

    same_request = session.scalar(
        select(ReviewUndoRun).where(
            ReviewUndoRun.review_bundle_id == review_bundle_id,
            ReviewUndoRun.idempotency_key == idempotency_key,
        )
    )
    existing = session.scalar(
        select(ReviewUndoRun).where(
            ReviewUndoRun.source_apply_run_id == apply_run_id
        )
    )
    run = same_request or existing
    if run is not None:
        existing_job = _active_or_completed_job(session, run)
        if same_request is not None and existing_job is not None:
            return ReviewUndoEnqueued(run.id, existing_job.id)
        if run.state == "undone":
            raise ReviewUndoError("this apply run has already been undone")
        if run.state in {"pending", "undoing"}:
            if existing_job is not None:
                return ReviewUndoEnqueued(run.id, existing_job.id)
            raise ReviewUndoError("undo is already in progress")
        files = run.manifest.get("files", [])
        if not isinstance(files, list) or not any(
            isinstance(entry, dict) and entry.get("retryable") is True
            for entry in files
        ):
            raise ReviewUndoError("undo failed closed and cannot be retried")
    else:
        run = _create_run(
            session,
            bundle,
            source_run,
            idempotency_key=idempotency_key,
        )

    payload: dict[str, object] = {"undo_run_id": run.id}
    if backup is not None:
        payload["backup"] = backup
    job = queue.enqueue(
        session,
        type="undo_review_bundle",
        payload=payload,
        commit=False,
    )
    manifest = dict(run.manifest)
    manifest["job_ids"] = [*_job_ids(run), job.id]
    run.manifest = manifest
    session.commit()
    return ReviewUndoEnqueued(run.id, job.id)
