"""The `enrich_replaygain` job type: computes ReplayGain for every group
(album or singleton) with at least one untagged track and stages the
result as an `enrichment` ChangeSet per group (docs/PLAN.md §Phase-6).

Sync, CPU-bound work (`rsgain`) runs in a thread via `asyncio.to_thread`,
same pattern as the `fingerprint` handler's `fpcalc` calls — "async only
at the edges" (CLAUDE.md). One group at a time rather than a shared
semaphore pool: `rsgain` already processes a whole album's files in one
subprocess call, so per-group concurrency would just contend for the
same CPU rsgain is already using internally.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session

from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.enrichment import groups_needing_replaygain, stage_replaygain_for_group


@register("enrich_replaygain")
async def handle_enrich_replaygain(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    if not context.config.enrichment.replaygain_enabled:
        progress.log("replaygain disabled in config — skipping")
        return {"analyzed": 0, "errored": 0, "skipped": True}

    groups = groups_needing_replaygain(session)
    total = len(groups)
    progress.update(0, total=total, message="computing replaygain")

    change_set_ids: list[int] = []
    errored = 0

    for i, group in enumerate(groups):
        if job.cancel_requested:
            raise JobCancelled
        try:
            change_set = await asyncio.to_thread(stage_replaygain_for_group, session, group)
        except Exception as exc:  # a bad file must never abort the whole job
            errored += 1
            progress.log(f"replaygain failed for group {group.id}: {exc}")
            continue
        if change_set is not None:
            change_set_ids.append(change_set.id)
        session.commit()
        progress.update(i + 1, total=total)

    progress.update(total, total=total, message="replaygain complete")
    return {
        "change_set_ids": change_set_ids,
        "analyzed": len(change_set_ids),
        "errored": errored,
    }
