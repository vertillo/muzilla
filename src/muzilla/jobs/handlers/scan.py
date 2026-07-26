"""The `scan` job type: walks a library root and upserts tracks.

Progress is indeterminate (`total=None`) for this stage — scan_library
doesn't report incremental progress today, and a rescan of an unchanged
library is already fast (seconds), so a spinner is enough for now
rather than threading a progress callback into pipeline/scan.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.pipeline.scan import scan_library


@register("scan")
async def handle_scan(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    root = Path(str(job.payload["root"]))
    progress.log(f"scanning {root}")
    # scan_library is sync (mutagen/os.scandir) — off the event loop per
    # "async only at the edges" (CLAUDE.md).
    stats = await asyncio.to_thread(scan_library, session, root)
    progress.update(1, total=1, message="scan complete")
    return {
        "scanned": stats.scanned,
        "added": stats.added,
        "updated": stats.updated,
        "unchanged": stats.unchanged,
        "errored": stats.errored,
        "missing": stats.missing,
    }
