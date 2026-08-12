"""The `enrich_replaygain` job type: computes ReplayGain for every group
(album or singleton) with at least one untagged track and stages the
result as an `enrichment` ChangeSet per group.

Sync, CPU-bound work (`rsgain`) runs in a thread via `asyncio.to_thread`.
One group at a time rather than a shared
semaphore pool: `rsgain` already processes a whole album's files in one
subprocess call, so per-group concurrency would just contend for the
same CPU rsgain is already using internally.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.audio.replaygain import (
    ReplayGainError,
    compute_album_replaygain,
    probe_replaygain_runtime,
)
from muzilla.db.models import Job
from muzilla.jobs.cancellation import current_token
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.enrichment import groups_needing_replaygain
from muzilla.pipeline.proposals import ProposalComposer
from muzilla.pipeline.reviews import OperationDraft, finish_task_attempt, start_task_attempt


@register("enrich_replaygain")
async def handle_enrich_replaygain(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    if not context.config.enrichment.replaygain_enabled:
        raise ReplayGainError("ReplayGain unavailable: disabled by configuration")

    available, detail = await asyncio.to_thread(probe_replaygain_runtime)
    if not available:
        raise ReplayGainError(f"ReplayGain unavailable: {detail}")

    groups = groups_needing_replaygain(session)
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
        groups = [
            group
            for group in groups
            if (bundle := ProposalComposer.open_bundle_for_group(session, group)) is not None
            and bundle.id in requested_bundle_ids
        ]
    total = len(groups)
    progress.update(0, total=total, message="computing replaygain")

    review_ids: list[int] = []
    errored = 0
    token = current_token(session, job.id)

    for i, group in enumerate(groups):
        if token.is_requested():
            raise JobCancelled
        bundle = ProposalComposer.open_bundle_for_group(session, group)
        if bundle is None:
            progress.update(i + 1, total=total)
            continue
        item_key = f"{bundle.scope_type}:{bundle.scope_id}"
        attempt = start_task_attempt(
            session, bundle.id, kind="replaygain", item_key=item_key, job_id=job.id
        )
        try:
            tracks = [track for track in group.tracks if track.missing_since is None]
            results = await asyncio.to_thread(
                compute_album_replaygain, [Path(track.path) for track in tracks]
            )
        except Exception as exc:  # a bad file must never abort the whole job
            errored += 1
            progress.log(f"replaygain failed for group {group.id}: {exc}")
            finish_task_attempt(session, attempt, state="transient_failure", error=str(exc))
            session.commit()
            progress.update(i + 1, total=total)
            continue
        if token.is_requested():
            session.rollback()
            raise JobCancelled
        operations: list[OperationDraft] = []
        for track in tracks:
            result = results.get(Path(track.path))
            if result is None:
                continue
            values: tuple[tuple[str, float | None], ...] = (
                ("rg_track_gain", result.track_gain_db),
                ("rg_track_peak", result.track_peak),
                ("rg_album_gain", result.album_gain_db),
                ("rg_album_peak", result.album_peak),
            )
            operations.extend(
                OperationDraft(
                    kind="set_replay_gain",
                    field=field,
                    target_type="track",
                    target_id=track.id,
                    current_value=getattr(track, field),
                    proposed_value=value,
                    provenance={"section": "audio", "analyzer": "rsgain"},
                )
                for field, value in values
                if value is not None
            )
        if not operations:
            errored += 1
            progress.log(f"replaygain produced no usable result for group {group.id}")
            finish_task_attempt(
                session, attempt, state="permanent_failure", error="no usable result"
            )
        else:
            ProposalComposer(session).add_operations(bundle.id, tuple(operations))
            review_ids.append(bundle.id)
            finish_task_attempt(
                session, attempt, state="succeeded", result={"review_bundle_id": bundle.id}
            )
        session.commit()
        progress.update(i + 1, total=total)

    if total > 0 and errored == total:
        raise ReplayGainError(f"ReplayGain failed for all {total} group(s)")

    progress.update(total, total=total, message="replaygain complete")
    return {
        "review_ids": review_ids,
        "analyzed": len(review_ids),
        "errored": errored,
    }
