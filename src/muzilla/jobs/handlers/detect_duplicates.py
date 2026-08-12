"""The `detect_duplicates` job type: runs fingerprint-based duplicate
detection (docs/product-spec.md, "duplicate detection by fingerprint,
not filename").

Purely DB-bound (no network, no CPU-heavy work — it only reads already-
computed TrackFingerprintMatch rows from Phase 3's fingerprinting),
so unlike the other enrichment handlers this doesn't need
asyncio.to_thread or per-item progress; it's a single fast pass.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.pipeline.duplicates import detect_duplicates


@register("detect_duplicates")
async def handle_detect_duplicates(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    progress.log("scanning for fingerprint duplicates")
    result = detect_duplicates(session)
    session.commit()
    progress.update(1, total=1, message="duplicate detection complete")
    return {
        "groups_created": result.groups_created,
        "groups_updated": result.groups_updated,
        "groups_dismissed_skipped": result.groups_dismissed_skipped,
        "groups_removed": result.groups_removed,
    }
