"""The `group` job type: runs the grouping cascade."""

from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session

from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.pipeline.grouping import run_grouping_cascade


@register("group")
async def handle_group(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    progress.log("running grouping cascade")
    result = await asyncio.to_thread(run_grouping_cascade, session)
    progress.update(1, total=1, message="grouping complete")
    return {
        "groups_created": result.groups_created,
        "groups_updated": result.groups_updated,
        "tracks_grouped": result.tracks_grouped,
        "tracks_skipped_pinned": result.tracks_skipped_pinned,
    }
