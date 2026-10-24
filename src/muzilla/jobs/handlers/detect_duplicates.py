"""The `detect_duplicates` job type: runs fingerprint-based duplicate
detection, not filename-based detection. Keep its full-library DB pass
off the worker event loop; at production scale it can take several seconds.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.sync import run_with_session
from muzilla.pipeline.duplicates import DuplicateDetectionResult, detect_duplicates


def _detect_duplicates_sync(session: Session) -> DuplicateDetectionResult:
    result = detect_duplicates(session)
    session.commit()
    return result


@register("detect_duplicates")
async def handle_detect_duplicates(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    progress.log("scanning for fingerprint duplicates")
    result = await run_with_session(
        context.session_factory, session, _detect_duplicates_sync
    )
    progress.update(1, total=1, message="duplicate detection complete")
    return {
        "groups_created": result.groups_created,
        "groups_updated": result.groups_updated,
        "groups_dismissed_skipped": result.groups_dismissed_skipped,
        "groups_removed": result.groups_removed,
    }
