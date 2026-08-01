"""The `import` job type: the parent orchestrator walking one
ImportSession's four ImportTask stages (scan, fingerprint, group,
match) in order, resumable from wherever a crash left off.

Deliberately calls the other handlers' functions **directly**, not by
sub-enqueuing separate jobs — there's no isolation benefit within one
worker task, and one Job row (type='import') stays the single
lease/cancel/SSE unit for the whole session. Each stage handler reads
its inputs from the *parent* `import` job's own payload (which
`services.imports.start_import` seeds with `root` and
`import_session_id` up front) — there is no separate per-stage Job
row, only per-stage `ImportTask` bookkeeping for resumability (skip
anything already state='done' on a re-run after a crash).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from muzilla.db.models import ImportSession, ImportTask, Job
from muzilla.jobs.cancellation import current_token
from muzilla.jobs.handlers.fingerprint import handle_fingerprint
from muzilla.jobs.handlers.group import handle_group
from muzilla.jobs.handlers.match import handle_match
from muzilla.jobs.handlers.scan import handle_scan
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled

_STAGE_HANDLERS = {
    "scan": handle_scan,
    "fingerprint": handle_fingerprint,
    "group": handle_group,
    "match": handle_match,
}
_STAGE_STATES = {
    "scan": "scanning",
    "fingerprint": "fingerprinting",
    "group": "grouping",
    "match": "matching",
}


@register("import")
async def handle_import(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    raw_import_session_id = job.payload["import_session_id"]
    assert isinstance(raw_import_session_id, int | str)
    import_session_id = int(raw_import_session_id)
    import_session = session.get(ImportSession, import_session_id)
    if import_session is None:
        raise ValueError(f"import session {import_session_id} not found")

    # Stage handlers read their inputs off `job.payload` (handle_scan
    # wants "root"; handle_match wants "import_session_id") — the
    # parent `import` job's payload already carries import_session_id
    # from enqueue time, so only "root" needs adding here.
    job.payload = {**job.payload, "root": import_session.library_root}

    tasks = sorted(import_session.tasks, key=lambda t: t.seq)
    total_stages = len(tasks)

    def cancellation_result(
        partial_task: ImportTask | None = None, partial_result: dict[str, object] | None = None
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "import_session_id": import_session_id,
            "state": "cancelled",
            "partial": True,
        }
        if partial_task is not None:
            result["cancelled_stage"] = partial_task.stage
        if partial_result is not None:
            result["stage_result"] = partial_result
        return result

    def cancel_session(
        partial_task: ImportTask | None = None, partial_result: dict[str, object] | None = None
    ) -> None:
        import_session.state = "cancelled"
        if partial_task is not None and partial_result is not None:
            partial_task.result = partial_result
            partial_task.updated_at = datetime.now(UTC)
        for pending_task in tasks:
            if pending_task.state != "done":
                pending_task.state = "cancelled"
        session.commit()

    token = current_token(session, job.id)
    for i, task in enumerate(tasks):
        if token.is_requested():
            cancel_session()
            raise JobCancelled(cancellation_result())

        if task.state == "done":
            continue

        import_session.state = _STAGE_STATES.get(task.stage, task.stage)
        task.state = "running"
        session.commit()

        progress.update(i, total=total_stages, message=f"stage: {task.stage}")
        progress.log(f"stage {task.stage} starting")

        handler = _STAGE_HANDLERS[task.stage]
        try:
            result = await handler(session, job, progress, context)
        except JobCancelled as exc:
            cancel_session(task, exc.result)
            raise JobCancelled(cancellation_result(task, exc.result)) from None
        except Exception as exc:
            task.state = "failed"
            task.error = str(exc)
            import_session.state = "failed"
            import_session.error = str(exc)
            session.commit()
            raise

        # A stage may have reached an item boundary immediately before this
        # orchestrator resumes.  Do not turn that cancellation into a
        # completed import/session merely because the child returned first.
        if token.is_requested(force=True):
            cancel_session(task, result)
            raise JobCancelled(cancellation_result(task, result))

        task.state = "done"
        task.result = result
        task.updated_at = datetime.now(UTC)
        session.commit()
        progress.log(f"stage {task.stage} done")

    import_session.state = "reviewing"
    session.commit()
    progress.update(total_stages, total=total_stages, message="import complete")
    return {"import_session_id": import_session_id, "state": import_session.state}
