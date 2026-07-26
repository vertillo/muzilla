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

from muzilla.db.models import ImportSession, Job
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

    for i, task in enumerate(tasks):
        if job.cancel_requested:
            import_session.state = "cancelled"
            session.commit()
            raise JobCancelled

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
        except Exception as exc:
            task.state = "failed"
            task.error = str(exc)
            import_session.state = "failed"
            import_session.error = str(exc)
            session.commit()
            raise

        task.state = "done"
        task.result = result
        task.updated_at = datetime.now(UTC)
        session.commit()
        progress.log(f"stage {task.stage} done")

    import_session.state = "reviewing"
    session.commit()
    progress.update(total_stages, total=total_stages, message="import complete")
    return {"import_session_id": import_session_id, "state": import_session.state}
