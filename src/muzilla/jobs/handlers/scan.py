"""The `scan` job type: walks a library root and upserts tracks.

Progress is indeterminate (`total=None`) for this stage — scan_library
doesn't report incremental progress today, and a rescan of an unchanged
library is already fast (seconds), so a spinner is enough for now
rather than threading a progress callback into pipeline/scan.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.orm import Session  # pyright: ignore[reportMissingImports]

from muzilla.db.models import Job
from muzilla.jobs.cancellation import current_token
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.scan import ScanCancelled, rescan_track, scan_library


@register("scan")
async def handle_scan(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    root = Path(str(job.payload["root"]))
    progress.log(f"scanning {root}")
    # scan_library is synchronous (mutagen/os.scandir); keep it off the
    # event loop.
    token = current_token(session, job.id)
    try:
        stats = await asyncio.to_thread(scan_library, session, root, should_cancel=token.is_requested)
    except ScanCancelled as exc:
        progress.log("scan cancelled; indexed files remain in the catalog")
        raise JobCancelled(
            {
                "scanned": exc.stats.scanned,
                "added": exc.stats.added,
                "updated": exc.stats.updated,
                "unchanged": exc.stats.unchanged,
                "errored": exc.stats.errored,
                "missing": exc.stats.missing,
                "partial": True,
            }
        ) from exc
    progress.update(1, total=1, message="scan complete")
    return {
        "scanned": stats.scanned,
        "added": stats.added,
        "updated": stats.updated,
        "unchanged": stats.unchanged,
        "errored": stats.errored,
        "missing": stats.missing,
    }


@register("rescan_track")
async def handle_rescan_track(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    raw_track_id = job.payload.get("track_id")
    if not isinstance(raw_track_id, int | str):
        raise ValueError("rescan_track requires an integer track_id")
    track_id = int(raw_track_id)
    progress.log(f"rereading track {track_id}")
    result = await asyncio.to_thread(
        rescan_track,
        session,
        track_id,
        library_root=context.config.storage.library_root,
    )
    progress.update(1, total=1, message="file reread")
    return {
        "track_id": result.track_id,
        "state": result.state,
        "fingerprint_invalidated": result.fingerprint_invalidated,
    }
