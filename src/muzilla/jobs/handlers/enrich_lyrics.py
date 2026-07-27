"""The `enrich_lyrics` job type: fetches lyrics from LRCLIB for every
track with title+artist and no lyrics yet, staging a `write_lyrics`
Change per track (docs/PLAN.md §Phase-6, "LRCLIB synced lyrics").

One track at a time — network-bound; LrcLibProvider already
rate-limits itself via providers/ratelimit.py, same reasoning as the
art handler's lack of extra concurrency control.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.enrichment import stage_lyrics_for_track, tracks_needing_lyrics


@register("enrich_lyrics")
async def handle_enrich_lyrics(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    if not context.config.enrichment.lyrics_enabled:
        progress.log("lyrics enrichment disabled in config — skipping")
        return {"found": 0, "not_found": 0, "errored": 0, "skipped": True}

    provider = context.provider_set.lyrics.get("lrclib")
    if provider is None:
        progress.log("lrclib not configured — skipping")
        return {"found": 0, "not_found": 0, "errored": 0, "skipped": True}

    tracks = tracks_needing_lyrics(session)
    total = len(tracks)
    progress.update(0, total=total, message="fetching lyrics")

    change_set_ids: list[int] = []
    not_found = 0
    errored = 0

    for i, track in enumerate(tracks):
        if job.cancel_requested:
            raise JobCancelled
        try:
            change_set = await stage_lyrics_for_track(session, track, provider)
        except Exception as exc:  # a bad fetch must never abort the whole job
            errored += 1
            progress.log(f"lyrics fetch failed for track {track.id}: {exc}")
            progress.update(i + 1, total=total)
            continue

        if change_set is None:
            not_found += 1
        else:
            change_set_ids.append(change_set.id)
        session.commit()
        progress.update(i + 1, total=total)

    progress.update(total, total=total, message="lyrics fetch complete")
    return {
        "change_set_ids": change_set_ids,
        "found": len(change_set_ids),
        "not_found": not_found,
        "errored": errored,
    }
