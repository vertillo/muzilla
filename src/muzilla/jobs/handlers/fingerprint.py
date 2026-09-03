"""The `fingerprint` job type: computes AcoustID fingerprints for
tracks that don't have one yet, then looks each up against AcoustID
and persists the results for grouping.

Bounded concurrency `min(4, cpu_count)` for the CPU-bound fpcalc calls
— fingerprinting a whole library at once would starve the API on a
mini-PC otherwise. Network lookups run at whatever rate
`get_limiter("acoustid")` allows; this handler doesn't add its own
extra throttling on top of that.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from sqlalchemy import select  # pyright: ignore[reportMissingImports]
from sqlalchemy.orm import Session  # pyright: ignore[reportMissingImports]

from muzilla.audio.fingerprint import FingerprintError, compute_fingerprint
from muzilla.db.models import Job, Track, TrackFingerprintMatch
from muzilla.jobs.cancellation import current_token
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.scan_constants import is_in_scope

_MAX_CONCURRENCY = min(4, os.cpu_count() or 1)


@register("fingerprint")
async def handle_fingerprint(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    provider = context.provider_set.fingerprint.get("acoustid")
    if provider is None:
        progress.log("acoustid not configured — skipping fingerprint stage")
        return {"fingerprinted": 0, "errored": 0, "skipped": True}

    tracks = list(
        session.scalars(
            select(Track).where(Track.missing_since.is_(None), Track.acoustid_fingerprint.is_(None))
        )
    )
    # Scoped import: only fingerprint tracks within the selected subtree/file.
    raw_root = job.payload.get("root")
    if isinstance(raw_root, str) and raw_root:
        try:
            scope_root = Path(raw_root).resolve()
        except OSError:
            scope_root = Path(raw_root)
        tracks = [t for t in tracks if is_in_scope(t.path, scope_root)]
    total = len(tracks)
    progress.update(0, total=total, message="fingerprinting")

    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    fingerprinted = 0
    errored = 0
    token = current_token(session, job.id)

    async def _process(index: int, track: Track) -> None:
        nonlocal fingerprinted, errored
        async with semaphore:
            if token.is_requested():
                return
            try:
                fp = await asyncio.to_thread(compute_fingerprint, Path(track.path))
            except FingerprintError as exc:
                errored += 1
                progress.log(f"fingerprint failed for {track.path}: {exc}")
                return

            track.acoustid_fingerprint = fp.fingerprint
            from muzilla.providers.base import FingerprintMatch as _FM
            from muzilla.providers.cache import cached_fingerprint_lookup

            _matches_any, _prov2 = await cached_fingerprint_lookup(
                session, context.config, provider, fp.fingerprint, fp.duration_s
            )
            matches: list[_FM] = []
            if isinstance(_matches_any, list):
                for m in _matches_any:
                    if isinstance(m, dict):
                        matches.append(
                            _FM(
                                mb_recording_id=str(m.get("mb_recording_id", "")),
                                mb_release_ids=tuple(m.get("mb_release_ids", ())),
                                score=float(m.get("score", 0.0)),
                            )
                        )
                    elif isinstance(m, _FM):
                        matches.append(m)
            if token.is_requested(force=True):
                session.rollback()
                raise JobCancelled(
                    {"fingerprinted": fingerprinted, "errored": errored, "partial": True}
                )
            for match in matches:
                session.add(
                    TrackFingerprintMatch(
                        track_id=track.id,
                        mb_recording_id=match.mb_recording_id,
                        mb_release_ids=list(match.mb_release_ids),
                        score=match.score,
                    )
                )
            session.commit()
            fingerprinted += 1
            progress.update(index + 1, total=total)

    for i, track in enumerate(tracks):
        if token.is_requested():
            raise JobCancelled
        await _process(i, track)

    progress.update(total, total=total, message="fingerprinting complete")
    return {"fingerprinted": fingerprinted, "errored": errored}
