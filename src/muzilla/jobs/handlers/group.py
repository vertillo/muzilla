"""The `group` job type: runs the grouping cascade."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.sync import run_with_session
from muzilla.pipeline.grouping import GroupingRunResult, run_grouping_cascade


def _run_grouping(session: Session, scope_root: Path | None) -> GroupingRunResult:
    result = run_grouping_cascade(session, scope_root)
    session.commit()
    return result


@register("group")
async def handle_group(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    progress.log("running grouping cascade")
    # Scoped import: only group tracks within the selected subtree/file.
    raw_root = job.payload.get("root")
    scope_root = None
    if isinstance(raw_root, str) and raw_root:
        try:
            scope_root = Path(raw_root).resolve()
        except OSError:
            scope_root = Path(raw_root)
    result = await run_with_session(
        context.session_factory,
        session,
        _run_grouping,
        scope_root,
    )
    session.expire_all()
    progress.update(1, total=1, message="grouping complete")
    return {
        "groups_created": result.groups_created,
        "groups_updated": result.groups_updated,
        "tracks_grouped": result.tracks_grouped,
        "tracks_skipped_pinned": result.tracks_skipped_pinned,
    }
