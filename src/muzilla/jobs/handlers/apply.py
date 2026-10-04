"""Synchronous ReviewBundle Apply and Undo work supervised off the event loop."""

from __future__ import annotations

from sqlalchemy.orm import Session  # pyright: ignore[reportMissingImports]

from muzilla.changes.backup import BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.bundle_applier import apply_review_run
from muzilla.changes.bundle_undo import apply_review_undo_run
from muzilla.db.models import Job
from muzilla.jobs.cancellation import CancellationToken, current_token
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.sync import run_with_session
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.effective_settings import effective_paths_config


def _apply_sync(
    session: Session,
    apply_run_id: int,
    backup: bool,
    context: WorkerContext,
    token: CancellationToken,
) -> dict[str, object]:
    effective_paths = effective_paths_config(session, context.config.paths)
    backup_store = None
    if backup and context.config.storage.backup_dir is not None:
        backup_store = BackupStore(
            context.config.storage.backup_dir,
            library_root=context.config.storage.library_root,
        )
    result = apply_review_run(
        session,
        apply_run_id,
        library_root=context.config.storage.library_root,
        create_directories=effective_paths.create_directories,
        blob_store=BlobStore(context.config.storage.blob_dir),
        backup_store=backup_store,
        should_cancel=token.is_requested,
    )
    response: dict[str, object] = {
        "apply_run_id": result.apply_run_id,
        "review_bundle_id": result.review_bundle_id,
        "state": result.state,
        "atomicity": "review_bundle",
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
        "recovery_required": result.recovery_required,
    }
    if result.cancelled:
        response["cancelled"] = True
        raise JobCancelled(response)
    return response


def _undo_sync(
    session: Session,
    undo_run_id: int,
    backup: bool,
    context: WorkerContext,
    token: CancellationToken,
) -> dict[str, object]:
    effective_paths = effective_paths_config(session, context.config.paths)
    backup_store = None
    if backup and context.config.storage.backup_dir is not None:
        backup_store = BackupStore(
            context.config.storage.backup_dir,
            library_root=context.config.storage.library_root,
        )
    result = apply_review_undo_run(
        session,
        undo_run_id,
        library_root=context.config.storage.library_root,
        create_directories=effective_paths.create_directories,
        blob_store=BlobStore(context.config.storage.blob_dir),
        backup_store=backup_store,
        should_cancel=token.is_requested,
    )
    response: dict[str, object] = {
        "undo_run_id": result.undo_run_id,
        "review_bundle_id": result.review_bundle_id,
        "source_apply_run_id": result.source_apply_run_id,
        "state": result.state,
        "atomicity": "review_bundle",
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
    if result.cancelled or result.recovery_required:
        response["recovery_required"] = result.recovery_required
    if result.cancelled:
        response["cancelled"] = True
        raise JobCancelled(response)
    return response


@register("apply_review_bundle")
async def handle_apply_review_bundle(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    raw_run_id = job.payload["apply_run_id"]
    if not isinstance(raw_run_id, int | str):
        raise ValueError("invalid apply_run_id")
    apply_run_id = int(raw_run_id)
    backup = bool(job.payload.get("backup", context.config.apply.backup))
    token = current_token(session, job.id, context.session_factory)
    progress.update(0, total=1, message="applying review bundle atomically")
    response = await run_with_session(
        context.session_factory, session, _apply_sync, apply_run_id, backup, context, token
    )
    progress.update(1, total=1, message="review apply complete")
    return response


@register("undo_review_bundle")
async def handle_undo_review_bundle(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    raw_run_id = job.payload["undo_run_id"]
    if not isinstance(raw_run_id, int | str):
        raise ValueError("invalid undo_run_id")
    undo_run_id = int(raw_run_id)
    backup = bool(job.payload.get("backup", context.config.apply.backup))
    token = current_token(session, job.id, context.session_factory)
    progress.update(0, total=1, message="restoring review bundle atomically")
    response = await run_with_session(
        context.session_factory, session, _undo_sync, undo_run_id, backup, context, token
    )
    progress.update(1, total=1, message="review restore complete")
    return response
