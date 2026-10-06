"""Application service for persistent ReviewBundle undo enqueue and retry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from muzilla.db.models import ApplyRun, Job, ReviewBundle, ReviewUndoRun, Track
from muzilla.db.transactions import begin_sqlite_write_transaction
from muzilla.jobs import queue

# Register the worker handler when this service is the API entry point.
from muzilla.jobs.handlers import apply as _apply_handler  # noqa: F401


class ReviewUndoError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReviewUndoEnqueued:
    undo_run_id: int
    job_id: int


def _is_true(value: object) -> bool:
    return isinstance(value, bool) and value


def _job_ids(run: ReviewUndoRun) -> list[int]:
    raw = run.manifest.get("job_ids", [])
    if not isinstance(raw, list):
        return []
    return [value for value in raw if isinstance(value, int)]


def _active_or_completed_job(session: Session, run: ReviewUndoRun) -> Job | None:
    ids = _job_ids(run)
    if not ids:
        return None
    jobs = {job.id: job for job in session.scalars(select(Job).where(Job.id.in_(ids)))}
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


def _manifest_for_inverses(session: Session, source_run: ApplyRun) -> dict[str, object]:
    """Freeze reverse-order journal intent before publishing an Undo job."""
    from muzilla.db.models import Operation, OperationAttempt, ReviewFileJournal

    expected_track_ids: set[int] = set()
    for attempt in session.scalars(
        select(OperationAttempt).where(OperationAttempt.apply_run_id == source_run.id)
    ):
        if attempt.state != "applied":
            continue
        operation = session.get(Operation, attempt.operation_id)
        if operation is not None and operation.target_type == "track":
            expected_track_ids.add(operation.target_id)

    journals = list(
        session.scalars(
            select(ReviewFileJournal)
            .where(ReviewFileJournal.apply_run_id == source_run.id)
            .order_by(ReviewFileJournal.id.desc())
        )
    )
    if not journals:
        raise ReviewUndoError("undo expired: source journals are unavailable")
    if any(journal.state != "done" for journal in journals):
        raise ReviewUndoError("source apply journals are not a complete applied checkpoint")

    journals_by_track: dict[int, list[ReviewFileJournal]] = {}
    for journal in journals:
        if journal.phase not in {"tags", "move", "grouping"}:
            raise ReviewUndoError(f"unsupported source journal phase for undo: {journal.phase}")
        journals_by_track.setdefault(journal.track_id, []).append(journal)
    if expected_track_ids and not set(journals_by_track).issuperset(expected_track_ids):
        missing = sorted(expected_track_ids - set(journals_by_track))
        raise ReviewUndoError(f"source apply is missing inverse journals for track(s) {missing}")

    files: list[dict[str, object]] = []
    for track_id in dict.fromkeys(journal.track_id for journal in journals):
        track = session.get(Track, track_id)
        if track is None:
            raise ReviewUndoError(f"track {track_id} is missing from the source apply")
        steps: list[dict[str, object]] = []
        for journal in journals_by_track[track_id]:
            before_blob = journal.before_blob
            if not isinstance(before_blob, dict):
                raise ReviewUndoError(f"source inverse payload is missing for track {track_id}")
            if journal.phase == "tags":
                if (
                    not isinstance(journal.after_hash, str)
                    or not isinstance(before_blob.get("__muzilla_physical_guard_before"), dict)
                    or not isinstance(before_blob.get("__muzilla_physical_guard_after"), dict)
                ):
                    raise ReviewUndoError(f"source tag inverse is incomplete for track {track_id}")
            elif journal.phase == "move":
                if (
                    not isinstance(journal.before_path, str)
                    or not isinstance(journal.after_path, str)
                    or not isinstance(before_blob.get("__muzilla_physical_guard_before"), dict)
                    or not isinstance(before_blob.get("__muzilla_physical_guard_after"), dict)
                ):
                    raise ReviewUndoError(f"source move inverse is incomplete for track {track_id}")
            elif journal.phase == "grouping" and not isinstance(before_blob.get("action"), str):
                raise ReviewUndoError(f"source grouping inverse is incomplete for track {track_id}")
            steps.append(
                {
                    "journal_id": journal.id,
                    "phase": journal.phase,
                    "path": journal.path,
                    "before_hash": journal.before_hash,
                    "after_hash": journal.after_hash,
                    "before_path": journal.before_path,
                    "after_path": journal.after_path,
                    "before_blob": dict(before_blob),
                }
            )
        files.append(
            {
                "track_id": track_id,
                "source": _track_checkpoint(track),
                "state": "pending",
                "error": None,
                "retryable": True,
                "steps": steps,
            }
        )
    if not files:
        raise ReviewUndoError("apply run has no successful file operations to undo")
    return {
        "version": 2,
        "source_apply_run_id": source_run.id,
        "files": files,
        "execution": {
            "completed_journal_ids": [],
            "active_journal_id": None,
            "step_checkpoints": {},
            "file_checkpoints": {},
        },
        "job_ids": [],
    }


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
            select(ReviewUndoRun).where(ReviewUndoRun.source_apply_run_id == source_run.id)
        )
        if existing is not None:
            return existing
        raise ReviewUndoError("review already has an active undo run")
    run = session.get(ReviewUndoRun, inserted_id)
    if run is None:  # pragma: no cover - inserted in this transaction
        raise ReviewUndoError("could not create undo run")
    run.manifest = _manifest_for_inverses(session, source_run)
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
    begin_sqlite_write_transaction(session)
    bundle = session.get(ReviewBundle, review_bundle_id)
    if bundle is None:
        raise ReviewUndoError(f"review bundle {review_bundle_id} not found")
    source_run = session.get(ApplyRun, apply_run_id)
    if source_run is None or source_run.review_bundle_id != review_bundle_id:
        raise ReviewUndoError("apply run does not belong to this review")
    if source_run.state not in {"applied", "partially_applied"}:
        raise ReviewUndoError("only an applied or partially applied run can be undone")
    # Fail closed when retention has pruned journals (age or count threshold)
    from muzilla.db.models import Operation as _OpCheck
    from muzilla.db.models import OperationAttempt as _OpAttemptCheck
    from muzilla.db.models import ReviewFileJournal as _RFJCheck

    _expected_check: set[int] = set()
    for _att in session.scalars(
        select(_OpAttemptCheck).where(
            _OpAttemptCheck.apply_run_id == source_run.id,
            _OpAttemptCheck.state == "applied",
        )
    ):
        _op_c = session.get(_OpCheck, _att.operation_id)
        if _op_c is not None and _op_c.target_type == "track":
            _expected_check.add(_op_c.target_id)
    journals_count = session.scalar(
        select(func.count()).select_from(_RFJCheck).where(_RFJCheck.apply_run_id == source_run.id)
    )
    if _expected_check and (journals_count or 0) == 0:
        raise ReviewUndoError(
            "undo expired: journal retention window elapsed (age/count threshold) — no journals retained"
        )
    if _expected_check:
        _j_tids = {
            row[0]
            for row in session.execute(
                select(_RFJCheck.track_id).where(_RFJCheck.apply_run_id == source_run.id)
            )
        }
        if not _j_tids.issuperset(_expected_check):
            _missing_c = sorted(_expected_check - _j_tids)
            raise ReviewUndoError(
                f"undo expired: journal retention window elapsed — missing journals for track(s) {_missing_c}"
            )

    same_request = session.scalar(
        select(ReviewUndoRun).where(
            ReviewUndoRun.review_bundle_id == review_bundle_id,
            ReviewUndoRun.idempotency_key == idempotency_key,
        )
    )
    existing = session.scalar(
        select(ReviewUndoRun).where(ReviewUndoRun.source_apply_run_id == apply_run_id)
    )
    run = same_request or existing
    if run is not None:
        existing_job = _active_or_completed_job(session, run)
        if same_request is not None and existing_job is not None:
            session.rollback()
            return ReviewUndoEnqueued(run.id, existing_job.id)
        if run.state == "undone":
            raise ReviewUndoError("this apply run has already been undone")
        if existing_job is not None:
            session.rollback()
            return ReviewUndoEnqueued(run.id, existing_job.id)
        if run.state in {"pending", "undoing"}:
            raise ReviewUndoError("undo is already in progress")
        result = run.result if isinstance(run.result, dict) else {}
        manifest_version = run.manifest.get("version")
        legacy_files = run.manifest.get("files", [])
        legacy_retryable = isinstance(legacy_files, list) and any(
            isinstance(entry, dict) and _is_true(entry.get("retryable")) for entry in legacy_files
        )
        if not _is_true(result.get("retryable")) and not (
            manifest_version != 2
            and (
                _is_true(result.get("cancelled"))
                or _is_true(result.get("recovery_required"))
                or legacy_retryable
            )
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
