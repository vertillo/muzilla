"""The `match` job type: proposes and auto-stages candidates for every
unmatched group, using the exact same orchestration
(`pipeline.matching`) an on-demand API request uses.

Album groups (`kind="album"`) match at the release level via
`propose_group_candidates`/`stage_group_match`; singleton groups
(`kind="singleton"`, exactly one track) match at the recording level
via `propose_track_candidates`/`stage_track_match` (docs/PLAN.md §7
step 5: "Albums match against releases; singletons match against
recordings"). Every changeset this handler stages is tagged with the
owning import_session_id (if any), so the review inbox can find it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import Job, TrackGroup
from muzilla.jobs import queue
from muzilla.jobs.cancellation import current_token
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.matching import (
    propose_group_candidates,
    propose_track_candidates,
)
from muzilla.pipeline.proposals import ProposalComposer
from muzilla.providers.base import ProviderRef


@register("match")
async def handle_match(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    if not context.config.enrichment.metadata_auto:
        # This is the automatic matching producer only.  Manual candidate
        # search/import remains available on an already-open ReviewBundle.
        progress.update(0, total=0, message="automatic metadata matching disabled")
        return {
            "proposed": 0,
            "skipped_no_candidates": 0,
            "enrichment_job_ids": [],
            "skipped": True,
        }

    groups = list(session.scalars(select(TrackGroup).where(TrackGroup.match_state == "unmatched")))
    total = len(groups)
    progress.update(0, total=total, message="matching")

    proposed = 0
    skipped_no_candidates = 0
    review_ids: list[int] = []
    token = current_token(session, job.id)

    def cancel_after_fetch() -> None:
        if token.is_requested(force=True):
            # Staging builds a draft in the worker session without committing
            # it.  A cancel that arrives during provider I/O must discard that
            # in-flight proposal before the import orchestrator sees it.
            session.rollback()
            raise JobCancelled(
                {
                    "proposed": proposed,
                    "skipped_no_candidates": skipped_no_candidates,
                    "partial": True,
                }
            )

    for i, group in enumerate(groups):
        if token.is_requested():
            raise JobCancelled

        review_id: int | None = None
        if group.kind == "singleton":
            tracks = list(group.tracks)
            if not tracks:
                skipped_no_candidates += 1
                progress.update(i + 1, total=total)
                continue
            track_proposal = await propose_track_candidates(
                session, context.provider_set, tracks[0].id
            )
            cancel_after_fetch()
            if not track_proposal.candidates:
                skipped_no_candidates += 1
                progress.update(i + 1, total=total)
                continue
            top = track_proposal.candidates[0]
            provider = context.provider_set.metadata[top.source]
            candidate = await provider.get_release(ProviderRef(provider=top.source, id=top.ref_id))
            if candidate is not None:
                review_id = (
                    ProposalComposer(session, paths_config=context.config.paths)
                    .compose_candidate_for_scope(
                        scope_type="track", scope_id=tracks[0].id, candidate=candidate
                    )
                    .id
                )
        else:
            group_proposal = await propose_group_candidates(session, context.provider_set, group.id)
            cancel_after_fetch()
            if not group_proposal.candidates:
                skipped_no_candidates += 1
                progress.update(i + 1, total=total)
                continue
            top = group_proposal.candidates[0]
            provider = context.provider_set.metadata[top.source]
            candidate = await provider.get_release(ProviderRef(provider=top.source, id=top.ref_id))
            if candidate is not None:
                review_id = (
                    ProposalComposer(session, paths_config=context.config.paths)
                    .compose_candidate_for_scope(
                        scope_type="group", scope_id=group.id, candidate=candidate
                    )
                    .id
                )

        cancel_after_fetch()

        if review_id is None:
            skipped_no_candidates += 1
            progress.update(i + 1, total=total)
            continue

        group.match_state = "proposed"
        session.commit()
        review_ids.append(review_id)
        proposed += 1
        progress.update(i + 1, total=total)

    enrichment_job_ids: list[int] = []
    # These are separate technical jobs.  They are intentionally enqueued only
    # after the metadata reviews exist, and never form a child transaction of
    # matching or of one another.
    task_jobs = (
        (
            "cover",
            "enrich_art",
            context.config.enrichment.art_auto,
            context.config.enrichment.network_priority,
        ),
        (
            "lyrics",
            "enrich_lyrics",
            context.config.enrichment.lyrics_auto,
            context.config.enrichment.network_priority,
        ),
        (
            "replaygain",
            "enrich_replaygain",
            context.config.enrichment.replaygain_auto,
            context.config.enrichment.replaygain_priority,
        ),
    )
    composer = ProposalComposer(session)
    try:
        for kind, job_type, enabled, priority in task_jobs:
            if not review_ids or not enabled:
                continue
            enrichment_job = queue.enqueue(
                session,
                type=job_type,
                payload={
                    "origin": "proposal_composer",
                    "review_bundle_ids": review_ids,
                },
                priority=priority,
                commit=False,
            )
            for review_id in review_ids:
                composer.plan_task_attempts(
                    review_id,
                    kind=kind,
                    job_id=enrichment_job.id,
                )
            enrichment_job_ids.append(enrichment_job.id)
        if enrichment_job_ids:
            # Job rows and their visible pending attempts become leaseable together.
            session.commit()
    except Exception:
        # Never let the worker supervisor's job-state commit publish a partial
        # set of section jobs without their matching pending attempts.
        session.rollback()
        raise
    progress.update(total, total=total, message="matching complete")
    return {
        "proposed": proposed,
        "skipped_no_candidates": skipped_no_candidates,
        "enrichment_job_ids": enrichment_job_ids,
    }
