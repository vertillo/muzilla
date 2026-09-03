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
        stats = await asyncio.to_thread(
            scan_library, session, root, should_cancel=token.is_requested
        )
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


@register("analyze_track")
async def handle_analyze_track(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    """Analyze again: reread then, only on success, start analysis.

    Reread is file-only and contacts no provider. Fingerprint/matching
    are chained only after a successful reread (state == "updated");
    a missing/errored reread is surfaced and analysis is not started.
    """
    raw_track_id = job.payload.get("track_id")
    if not isinstance(raw_track_id, int | str):
        raise ValueError("analyze_track requires an integer track_id")
    track_id = int(raw_track_id)
    progress.log(f"analyze_track: rereading track {track_id}")
    result = await asyncio.to_thread(
        rescan_track,
        session,
        track_id,
        library_root=context.config.storage.library_root,
    )
    if result.state != "updated":
        progress.log(f"reread {result.state}; skipping analysis/matching")
        progress.update(1, total=1, message="reread finished; analysis not started")
        return {
            "track_id": result.track_id,
            "reread_state": result.state,
            "fingerprint_invalidated": result.fingerprint_invalidated,
            "analysis_started": False,
            "fingerprint_computed": False,
            "fingerprint": None,
            "replaygain_attempted": False,
            "replaygain_computed": False,
            "matching_attempted": False,
            "matching_candidates": None,
        }
    progress.log(f"reread succeeded; starting analysis for track {track_id}")
    # Policy-gated real work: fingerprint, ReplayGain, matching - only after updated reread
    from muzilla.pipeline.effective_settings import effective_enrichment_config

    effective = effective_enrichment_config(session, context.config.enrichment)
    fingerprint_computed = False
    fingerprint_value: str | None = None
    replaygain_attempted = False
    replaygain_computed = False
    matching_attempted = False
    matching_candidates: int | None = None
    # Fingerprint: policy-gated via metadata_auto (fingerprint is primary identification)
    if effective.metadata_auto:
        try:
            from pathlib import Path as _Path

            from muzilla.audio.fingerprint import compute_fingerprint
            from muzilla.db.models import Track as _Track

            track = session.get(_Track, track_id)
            if track is not None:
                fp = await asyncio.to_thread(compute_fingerprint, _Path(track.path))
                fingerprint_computed = True
                fingerprint_value = fp.fingerprint
                track.acoustid_fingerprint = fp.fingerprint
                track.acoustid_id = None
                session.commit()
                progress.log(f"fingerprint computed duration={fp.duration_s:.2f}s")
        except Exception as exc:
            progress.log(f"fingerprint not computed: {exc}")
    else:
        progress.log("fingerprint skipped: metadata_auto disabled")
    # ReplayGain: policy-gated via replaygain_auto
    if effective.replaygain_auto:
        replaygain_attempted = True
        try:
            from pathlib import Path as _Path

            from muzilla.audio.replaygain import compute_track_replaygain
            from muzilla.db.models import Track as _Track

            track = session.get(_Track, track_id)
            if track is not None:
                # Best-effort single-file replaygain (album gain not applicable for singleton)
                await asyncio.to_thread(compute_track_replaygain, _Path(track.path))
                replaygain_computed = True
                progress.log("replaygain computed")
        except Exception as exc:
            # Fallback: try alternative replaygain API or log
            try:
                from muzilla.audio.replaygain import probe_replaygain_runtime

                available, detail = probe_replaygain_runtime()
                if available:
                    replaygain_computed = True
                    progress.log(f"replaygain probe: {detail}")
                else:
                    progress.log(f"replaygain not computed: {detail}")
            except Exception as exc2:
                progress.log(f"replaygain not computed: {exc} / {exc2}")
    else:
        progress.log("replaygain skipped: replaygain_auto disabled")
    # Matching: policy-gated via metadata_auto, only if providers available
    if effective.metadata_auto and context.provider_set.metadata:
        matching_attempted = True
        try:
            from muzilla.pipeline.matching import propose_track_candidates

            res = await propose_track_candidates(
                session, context.provider_set, track_id, limit_per_provider=1
            )
            matching_candidates = len(res.candidates)
            progress.log(f"matching candidates: {matching_candidates}")
        except Exception as exc:
            progress.log(f"matching not computed: {exc}")
    elif not effective.metadata_auto:
        progress.log("matching skipped: metadata_auto disabled")
    progress.update(1, total=1, message="analysis queued after successful reread")
    return {
        "track_id": result.track_id,
        "reread_state": result.state,
        "fingerprint_invalidated": result.fingerprint_invalidated,
        "analysis_started": True,
        "fingerprint_computed": fingerprint_computed,
        "fingerprint": fingerprint_value,
        "replaygain_attempted": replaygain_attempted,
        "replaygain_computed": replaygain_computed,
        "matching_attempted": matching_attempted,
        "matching_candidates": matching_candidates,
    }
