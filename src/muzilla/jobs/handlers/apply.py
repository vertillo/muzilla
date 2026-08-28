"""The `apply_changeset`/`undo_changeset` job types: route the highest-risk
write path in the project through the same single-writer worker
as everything else, rather than running inline in the API request
handler as an incidental second writer.

Neither handler streams per-file progress today — apply/undo already
run fast enough that a single before/after progress tick is enough;
the point of moving them behind the job queue is single-writer
discipline and a uniform 202+job_id/SSE UX, not throughput.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.changes.backup import BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.bundle_applier import apply_review_run
from muzilla.changes.bundle_undo import apply_review_undo_run
from muzilla.db.models import Job
from muzilla.jobs.cancellation import current_token
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled


@register("apply_review_bundle")
async def handle_apply_review_bundle(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    raw_run_id = job.payload["apply_run_id"]
    assert isinstance(raw_run_id, int | str)
    apply_run_id = int(raw_run_id)
    backup = bool(job.payload.get("backup", context.config.apply.backup))
    backup_store = None
    if backup and context.config.storage.backup_dir is not None:
        backup_store = BackupStore(
            context.config.storage.backup_dir,
            library_root=context.config.storage.library_root,
        )
    token = current_token(session, job.id)
    progress.update(0, total=1, message="applying review per file")
    result = apply_review_run(
        session,
        apply_run_id,
        library_root=context.config.storage.library_root,
        create_directories=context.config.paths.create_directories,
        blob_store=BlobStore(context.config.storage.blob_dir),
        backup_store=backup_store,
        should_cancel=token.is_requested,
    )
    response: dict[str, object] = {
        "apply_run_id": result.apply_run_id,
        "review_bundle_id": result.review_bundle_id,
        "state": result.state,
        "atomicity": "per_file",
        "files": [
            {
                "track_id": file.track_id,
                "state": file.state,
                "applied_operation_ids": list(file.applied_operation_ids),
                "error": file.error,
            }
            for file in result.files
        ],
        "errors": result.errors,
    }
    if result.cancelled:
        response["partial"] = True
        raise JobCancelled(response)
    progress.update(1, total=1, message="review apply complete")
    return response


@register("undo_review_bundle")
async def handle_undo_review_bundle(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    raw_run_id = job.payload["undo_run_id"]
    assert isinstance(raw_run_id, int | str)
    undo_run_id = int(raw_run_id)
    backup = bool(job.payload.get("backup", context.config.apply.backup))
    backup_store = None
    if backup and context.config.storage.backup_dir is not None:
        backup_store = BackupStore(
            context.config.storage.backup_dir,
            library_root=context.config.storage.library_root,
        )
    token = current_token(session, job.id)
    progress.update(0, total=1, message="restoring review per file")
    result = apply_review_undo_run(
        session,
        undo_run_id,
        library_root=context.config.storage.library_root,
        create_directories=context.config.paths.create_directories,
        blob_store=BlobStore(context.config.storage.blob_dir),
        backup_store=backup_store,
        should_cancel=token.is_requested,
    )
    response: dict[str, object] = {
        "undo_run_id": result.undo_run_id,
        "review_bundle_id": result.review_bundle_id,
        "source_apply_run_id": result.source_apply_run_id,
        "state": result.state,
        "atomicity": "per_file",
        "files": [
            {
                "track_id": file.track_id,
                "state": file.state,
                "source_change_set_ids": list(file.source_change_set_ids),
                "error": file.error,
                "retryable": file.retryable,
            }
            for file in result.files
        ],
        "errors": result.errors,
    }
    if result.cancelled:
        response["partial"] = True
        raise JobCancelled(response)
    progress.update(1, total=1, message="review restore complete")
    return response
