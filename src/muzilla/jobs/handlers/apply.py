"""The `apply_changeset`/`undo_changeset` job types: routes the highest-
risk write path in the project (docs/PLAN.md's own "Critical Files"
callout for changes/applier.py) through the same single-writer worker
as everything else, rather than running inline in the API request
handler as an incidental second writer.

Neither handler streams per-file progress today — apply/undo already
run fast enough that a single before/after progress tick is enough;
the point of moving them behind the job queue is single-writer
discipline and a uniform 202+job_id/SSE UX, not throughput.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.changes.applier import apply_changeset
from muzilla.changes.backup import BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.undo import build_undo_changeset
from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.logging import change_set_context


@register("apply_changeset")
async def handle_apply_changeset(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    raw_change_set_id = job.payload["change_set_id"]
    assert isinstance(raw_change_set_id, int | str)
    change_set_id = int(raw_change_set_id)
    # payload["backup"] overrides config default when the caller passed
    # one explicitly (docs/PLAN.md §11b); omitted -> fall back to
    # apply.backup so `muzilla changes apply` without --backup still
    # respects an operator's configured default.
    backup = bool(job.payload.get("backup", context.config.apply.backup))

    backup_store = None
    if backup and context.config.storage.backup_dir is not None:
        backup_store = BackupStore(
            context.config.storage.backup_dir, library_root=context.config.storage.library_root
        )

    progress.update(0, total=1, message="applying")
    with change_set_context(change_set_id):
        result = apply_changeset(
            session,
            change_set_id,
            library_root=context.config.storage.library_root,
            create_directories=context.config.paths.create_directories,
            blob_store=BlobStore(context.config.storage.blob_dir),
            backup_store=backup_store,
        )
    session.commit()
    progress.update(1, total=1, message="apply complete")
    return {
        "change_set_id": result.change_set_id,
        "state": result.state,
        "applied_track_ids": result.applied_track_ids,
        "conflicted_track_ids": result.conflicted_track_ids,
        "errors": result.errors,
    }


@register("undo_changeset")
async def handle_undo_changeset(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    raw_change_set_id = job.payload["change_set_id"]
    assert isinstance(raw_change_set_id, int | str)
    change_set_id = int(raw_change_set_id)

    progress.update(0, total=1, message="building undo changeset")
    with change_set_context(change_set_id):
        undo_cs = build_undo_changeset(session, change_set_id)
    session.commit()
    progress.update(1, total=1, message="staged undo changeset")
    return {"undo_change_set_id": undo_cs.id}
