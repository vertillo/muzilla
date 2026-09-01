"""The `enrich_lyrics` job type: fetches lyrics from LRCLIB for every
track with title+artist and no lyrics yet, staging a `write_lyrics`
Change per track.

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
from muzilla.pipeline.effective_settings import effective_enrichment_config
from muzilla.pipeline.enrichment import tracks_needing_lyrics
from muzilla.pipeline.proposals import ProposalComposer
from muzilla.pipeline.reviews import OperationDraft, finish_task_attempt, start_task_attempt
from muzilla.providers.errors import ProviderError


@register("enrich_lyrics")
async def handle_enrich_lyrics(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    effective_enrichment = effective_enrichment_config(session, context.config.enrichment)
    if not effective_enrichment.lyrics_enabled:
        progress.log("lyrics enrichment disabled in config — skipping")
        return {"found": 0, "not_found": 0, "errored": 0, "items": [], "skipped": True}

    provider = context.provider_set.lyrics.get("lrclib")
    if provider is None:
        progress.log("lrclib not configured — skipping")
        return {"found": 0, "not_found": 0, "errored": 0, "items": [], "skipped": True}

    tracks = tracks_needing_lyrics(session)
    requested_ids = job.payload.get("track_ids")
    if requested_ids is not None:
        if not isinstance(requested_ids, list) or not all(
            isinstance(track_id, int) for track_id in requested_ids
        ):
            raise ValueError("track_ids must be a list of integers")
        requested = set(requested_ids)
        tracks = [track for track in tracks if track.id in requested]
    requested_bundle_ids: set[int] = set()
    requested_bundle_id = job.payload.get("review_bundle_id")
    if isinstance(requested_bundle_id, int):
        requested_bundle_ids.add(requested_bundle_id)
    raw_bundle_ids = job.payload.get("review_bundle_ids")
    if isinstance(raw_bundle_ids, list):
        requested_bundle_ids.update(
            bundle_id for bundle_id in raw_bundle_ids if isinstance(bundle_id, int)
        )
    if requested_bundle_ids:
        tracks = [
            track
            for track in tracks
            if (bundle := ProposalComposer.open_bundle_for_track(session, track)) is not None
            and bundle.id in requested_bundle_ids
        ]
    raw_item_keys = job.payload.get("item_keys")
    if isinstance(raw_item_keys, list):
        requested_item_keys = {key for key in raw_item_keys if isinstance(key, str)}
        tracks = [track for track in tracks if f"track:{track.id}" in requested_item_keys]
    total = len(tracks)
    progress.update(0, total=total, message="fetching lyrics")

    review_ids: list[int] = []
    not_found = 0
    errored = 0
    retryable_track_ids: list[int] = []
    items: list[dict[str, object]] = []
    token = current_token(session, job.id)

    for i, track in enumerate(tracks):
        if token.is_requested():
            raise JobCancelled(
                {
                    "review_ids": review_ids,
                    "found": len(review_ids),
                    "not_found": not_found,
                    "errored": errored,
                    "retryable_track_ids": retryable_track_ids,
                    "items": items,
                    "partial": True,
                }
            )
        bundle = ProposalComposer.open_bundle_for_track(session, track)
        if bundle is None:
            # Legacy/non-review catalog rows are deliberately ignored by the
            # new flow instead of gaining a visible enrichment ChangeSet.
            progress.update(i + 1, total=total)
            continue
        attempt = start_task_attempt(
            session, bundle.id, kind="lyrics", item_key=f"track:{track.id}", job_id=job.id
        )
        # Make the running attempt visible before provider I/O.  Keeping this
        # write transaction open while waiting on the network would prevent a
        # concurrent cancellation request from updating the SQLite job row.
        session.commit()
        try:
            from typing import cast

            from muzilla.domain.metadata import LyricsResult
            from muzilla.providers.cache import cached_get_lyrics

            _res, _prov = await cached_get_lyrics(
                session, context.config, provider, track.artist or "", track.title or "", track.duration_ms
            )
            result = cast(LyricsResult | None, _res)
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
            finish_task_attempt(
                session,
                attempt,
                state="transient_failure" if exc.retryable else "permanent_failure",
                error=str(exc),
            )
            session.commit()
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
            finish_task_attempt(session, attempt, state="permanent_failure", error=str(exc))
            session.commit()
            progress.update(i + 1, total=total)
            continue

        if token.is_requested():
            session.rollback()
            raise JobCancelled(
                {
                    "review_ids": review_ids,
                    "found": len(review_ids),
                    "not_found": not_found,
                    "errored": errored,
                    "retryable_track_ids": retryable_track_ids,
                    "items": items,
                    "partial": True,
                }
            )
        if result is None:
            not_found += 1
            items.append({"track_id": track.id, "outcome": "not_found", "retryable": False})
            finish_task_attempt(session, attempt, state="not_found")
        else:
            ProposalComposer(session).add_operations(
                bundle.id,
                (
                    OperationDraft(
                        kind="write_lyrics",
                        field="lyrics",
                        target_type="track",
                        target_id=track.id,
                        current_value=None,
                        proposed_value={
                            "text": result.text,
                            "synced": result.synced,
                            "provider": result.source,
                        },
                        provenance={"section": "lyrics", "provider": result.source},
                    ),
                ),
            )
            review_ids.append(bundle.id)
            items.append(
                {
                    "track_id": track.id,
                    "outcome": "found",
                    "retryable": False,
                    "review_bundle_id": bundle.id,
                }
            )
            finish_task_attempt(
                session, attempt, state="succeeded", result={"review_bundle_id": bundle.id}
            )
        session.commit()
        progress.update(i + 1, total=total)

    progress.update(total, total=total, message="lyrics fetch complete")
    return {
        "review_ids": review_ids,
        "found": len(review_ids),
        "not_found": not_found,
        "errored": errored,
        "retryable_track_ids": retryable_track_ids,
        "items": items,
    }
