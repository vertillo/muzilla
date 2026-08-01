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
from muzilla.jobs.cancellation import current_token
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.enrichment import stage_lyrics_for_track, tracks_needing_lyrics
from muzilla.providers.errors import ProviderError


@register("enrich_lyrics")
async def handle_enrich_lyrics(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    if not context.config.enrichment.lyrics_enabled:
        progress.log("lyrics enrichment disabled in config — skipping")
        return {"found": 0, "not_found": 0, "errored": 0, "items": [], "skipped": True}

    provider = context.provider_set.lyrics.get("lrclib")
    if provider is None:
        progress.log("lrclib not configured — skipping")
        return {"found": 0, "not_found": 0, "errored": 0, "items": [], "skipped": True}

    tracks = tracks_needing_lyrics(session)
    requested_ids = job.payload.get("track_ids")
    if requested_ids is not None:
        if not isinstance(requested_ids, list) or not all(isinstance(track_id, int) for track_id in requested_ids):
            raise ValueError("track_ids must be a list of integers")
        requested = set(requested_ids)
        tracks = [track for track in tracks if track.id in requested]
    total = len(tracks)
    progress.update(0, total=total, message="fetching lyrics")

    change_set_ids: list[int] = []
    not_found = 0
    errored = 0
    retryable_track_ids: list[int] = []
    items: list[dict[str, object]] = []
    token = current_token(session, job.id)

    for i, track in enumerate(tracks):
        if token.is_requested():
            raise JobCancelled(
                {
                    "change_set_ids": change_set_ids,
                    "found": len(change_set_ids),
                    "not_found": not_found,
                    "errored": errored,
                    "retryable_track_ids": retryable_track_ids,
                    "items": items,
                    "partial": True,
                }
            )
        try:
            change_set = await stage_lyrics_for_track(session, track, provider)
        except ProviderError as exc:
            errored += 1
            progress.log(f"lyrics fetch failed for track {track.id}: {exc}")
            item = {
                "track_id": track.id,
                "outcome": exc.outcome,
                "retryable": exc.retryable,
                "error": str(exc),
            }
            items.append(item)
            if exc.retryable:
                retryable_track_ids.append(track.id)
            progress.update(i + 1, total=total)
            continue
        except Exception as exc:  # an unexpected bad item must not abort the bulk job
            errored += 1
            progress.log(f"lyrics fetch failed for track {track.id}: {exc}")
            items.append(
                {
                    "track_id": track.id,
                    "outcome": "permanent_error",
                    "retryable": False,
                    "error": str(exc),
                }
            )
            progress.update(i + 1, total=total)
            continue

        if token.is_requested():
            # stage_lyrics_for_track builds a ChangeSet in this session but
            # intentionally does not commit it.  Roll it back so the worker's
            # subsequent state transition cannot accidentally persist a
            # proposal from an interrupted item.
            session.rollback()
            raise JobCancelled(
                {
                    "change_set_ids": change_set_ids,
                    "found": len(change_set_ids),
                    "not_found": not_found,
                    "errored": errored,
                    "retryable_track_ids": retryable_track_ids,
                    "items": items,
                    "partial": True,
                }
            )
        if change_set is None:
            not_found += 1
            items.append({"track_id": track.id, "outcome": "not_found", "retryable": False})
        else:
            change_set_ids.append(change_set.id)
            items.append(
                {
                    "track_id": track.id,
                    "outcome": "found",
                    "retryable": False,
                    "change_set_id": change_set.id,
                }
            )
        session.commit()
        progress.update(i + 1, total=total)

    progress.update(total, total=total, message="lyrics fetch complete")
    return {
        "change_set_ids": change_set_ids,
        "found": len(change_set_ids),
        "not_found": not_found,
        "errored": errored,
        "retryable_track_ids": retryable_track_ids,
        "items": items,
    }
