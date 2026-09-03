"""The `retention_sweep` job type: prunes
ApplyJournal rows past their age/count threshold and expired
ProviderCache rows. Purely DB-bound, single fast pass — same shape as
detect_duplicates.py's handler.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.pipeline.effective_settings import effective_retention_config
from muzilla.pipeline.retention import run_retention_sweep


@register("retention_sweep")
async def handle_retention_sweep(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    effective = effective_retention_config(session, context.config.retention)
    progress.log("sweeping apply journals and provider cache")
    result = run_retention_sweep(
        session,
        journal_days=effective.journal_days,
        journal_changesets=effective.journal_changesets,
    )
    session.commit()
    progress.update(1, total=1, message="retention sweep complete")
    return {
        "journals_pruned": result.journals_pruned,
        "changesets_marked_expired": result.changesets_marked_expired,
        "provider_cache_rows_pruned": result.provider_cache_rows_pruned,
    }
