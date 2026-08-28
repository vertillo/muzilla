"""The `match` job type: proposes and auto-stages candidates for every
unmatched group, using the exact same orchestration
(`pipeline.matching`) an on-demand API request uses.

Album groups (`kind="album"`) match at the release level via
`propose_group_candidates`/`stage_group_match`; singleton groups
(`kind="singleton"`, exactly one track) match at the recording level
via `propose_track_candidates`/`stage_track_match`. Albums match against
releases; singleton groups match against recordings. Every changeset this
handler stages is tagged with the
owning import_session_id (if any), so the review inbox can find it.
"""

from __future__ import annotations

from sqlalchemy import select  # pyright: ignore[reportMissingImports]
from sqlalchemy.orm import Session  # pyright: ignore[reportMissingImports]

from muzilla.db.models import Job, ReviewBundle, Track, TrackGroup
from muzilla.jobs import queue
from muzilla.jobs.cancellation import current_token
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled
from muzilla.matching.candidates import ProviderSearchOutcome
from muzilla.pipeline.matching import (
    CandidateRow,
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
    current_created_review_id: int | None = None
    raw_import_session_id = job.payload.get("import_session_id")
    import_session_id = int(raw_import_session_id) if isinstance(raw_import_session_id, int | str) else None
    composer = ProposalComposer(session, paths_config=context.config.paths)
    token = current_token(session, job.id)

    def cancel_after_fetch() -> None:
        if token.is_requested(force=True):
            # Staging builds a draft in the worker session without committing
            # it.  A cancel that arrives during provider I/O must discard that
            # in-flight proposal before the import orchestrator sees it.
            session.rollback()
            # A test/provider boundary may have committed the worker session while
            # cancellation was in flight.  The just-created stable row is still an
            # in-flight proposal in that case, so remove only that row; completed
            # import items remain visible and resumable.
            if current_created_review_id is not None:
                created = session.get(ReviewBundle, current_created_review_id)
                if created is not None:
                    session.delete(created)
                    session.commit()
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
        scope_type = "track" if group.kind == "singleton" else "group"
        scope_id = (
            next(iter(group.tracks)).id
            if group.kind == "singleton" and group.tracks
            else group.id
        )
        if group.kind == "singleton" and not group.tracks:
            skipped_no_candidates += 1
            progress.update(i + 1, total=total)
            continue
        if import_session_id is not None:
            existing = composer.open_bundle_for_scope(
                session, scope_type=scope_type, scope_id=scope_id
            )
            if existing is not None:
                review_id = existing.id
                if existing.import_session_id is None:
                    existing.import_session_id = import_session_id
            else:
                review_id = composer.prepare_import_scope(
                    scope_type=scope_type, scope_id=scope_id, import_session_id=import_session_id
                ).id
                current_created_review_id = review_id
        else:
            # Direct automatic matching keeps the same stable identity even outside an import.
            prepared = composer.open_bundle_for_scope(
                session, scope_type=scope_type, scope_id=scope_id
            )
            if prepared is None:
                if scope_type == "track":
                    track = session.get(Track, scope_id)
                    label = track.filename if track is not None else str(scope_id)
                else:
                    label = group.album or "Untitled"
                prepared = ReviewBundle(
                    logical_key=f"{scope_type}:{scope_id}", title=f"Review {label}",
                    scope_type=scope_type, scope_id=scope_id, state="preparing"
                )
                session.add(prepared)
                session.flush()
                current_created_review_id = prepared.id
            review_id = prepared.id
        if group.kind == "singleton":
            tracks = list(group.tracks)
            track_proposal = await propose_track_candidates(
                session, context.provider_set, tracks[0].id
            )
            cancel_after_fetch()
            if not track_proposal.candidates:
                review = session.get(ReviewBundle, review_id)
                assert review is not None
                composer.mark_match_needs_attention(
                    review,
                    outcome=_match_outcome(track_proposal.provider_outcomes, track_proposal.rejection_reason),
                    explanation=_match_explanation(track_proposal.provider_outcomes, track_proposal.rejection_reason),
                )
                skipped_no_candidates += 1
                progress.update(i + 1, total=total)
                continue
            top = track_proposal.candidates[0]
            provider = context.provider_set.metadata[top.source]
            candidate = await provider.get_release(ProviderRef(provider=top.source, id=top.ref_id))
            if candidate is not None:
                review = session.get(ReviewBundle, review_id)
                assert review is not None
                composer.compose_candidate(
                    review, candidate,
                    candidate_snapshot=_candidate_snapshot(top),
                    match_explanation=_match_explanation(
                        track_proposal.provider_outcomes, track_proposal.rejection_reason, top
                    ),
                    confidence=_confidence(top.adjusted_distance),
                )
            else:
                review = session.get(ReviewBundle, review_id)
                assert review is not None
                composer.mark_match_needs_attention(
                    review,
                    outcome="provider_failure",
                    explanation=_match_explanation(track_proposal.provider_outcomes, "candidate hydrate failed"),
                )
        else:
            group_proposal = await propose_group_candidates(session, context.provider_set, group.id)
            cancel_after_fetch()
            if not group_proposal.candidates:
                review = session.get(ReviewBundle, review_id)
                assert review is not None
                composer.mark_match_needs_attention(
                    review,
                    outcome=_match_outcome(group_proposal.provider_outcomes, group_proposal.rejection_reason),
                    explanation=_match_explanation(group_proposal.provider_outcomes, group_proposal.rejection_reason),
                )
                skipped_no_candidates += 1
                progress.update(i + 1, total=total)
                continue
            top = group_proposal.candidates[0]
            provider = context.provider_set.metadata[top.source]
            candidate = await provider.get_release(ProviderRef(provider=top.source, id=top.ref_id))
            if candidate is not None:
                review = session.get(ReviewBundle, review_id)
                assert review is not None
                composer.compose_candidate(
                    review, candidate,
                    candidate_snapshot=_candidate_snapshot(top),
                    match_explanation=_match_explanation(
                        group_proposal.provider_outcomes, group_proposal.rejection_reason, top
                    ),
                    confidence=_confidence(top.adjusted_distance),
                )
            else:
                review = session.get(ReviewBundle, review_id)
                assert review is not None
                composer.mark_match_needs_attention(
                    review,
                    outcome="provider_failure",
                    explanation=_match_explanation(group_proposal.provider_outcomes, "candidate hydrate failed"),
                )

        cancel_after_fetch()

        if review_id is None:
            # A candidate disappeared during hydrate. Keep the pre-created review usable.
            skipped_no_candidates += 1
            progress.update(i + 1, total=total)
            continue

        group.match_state = "proposed"
        session.commit()
        current_created_review_id = None
        review_ids.append(review_id)
        proposed += 1
        progress.update(i + 1, total=total)

    if token.is_requested(force=True):
        session.rollback()
        raise JobCancelled(
            {
                "proposed": proposed,
                "skipped_no_candidates": skipped_no_candidates,
                "partial": True,
            }
        )
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


def _confidence(distance: float) -> float:
    return max(0.0, min(1.0, 1.0 - distance))


def _candidate_snapshot(row: CandidateRow) -> dict[str, object]:
    """Serialize only server-scored candidate evidence for an immutable revision."""
    signals: list[dict[str, float | str]] = [
        {"field": signal.field, "distance": signal.distance, "weight": signal.weight,
         "contribution": signal.contribution}
        for signal in row.score_signals
    ]
    penalties = [
        {"field": signal.field, "distance": signal.distance, "weight": signal.weight,
         "contribution": signal.contribution}
        for signal in row.score_signals
        if signal.contribution > 0
    ]
    return {
        "provider": row.source, "ref": row.ref_id, "type": row.candidate_type,
        "title": row.representative_title, "artist": row.representative_artist,
        "album": row.album, "year": row.year,
        "duration_ms": row.representative_duration_ms,
        "position": row.representative_position, "track_count": row.track_count,
        "thumbnail": row.cover_url,
        "confidence_band": "high" if _confidence(row.adjusted_distance) >= 0.85 else "medium",
        "signals": signals,
        "penalties": penalties,
        "rejection_reasons": [row.rejection_reason] if row.rejection_reason else [],
    }


def _match_explanation(
    outcomes: tuple[ProviderSearchOutcome, ...], rejection_reason: str | None,
    row: CandidateRow | None = None,
) -> dict[str, object]:
    provider_outcomes = [
        {"provider": item.provider, "status": item.status, "result_count": item.result_count,
         "detail": item.detail}
        for item in outcomes
    ]
    result: dict[str, object] = {
        "provider_outcomes": provider_outcomes,
        "rejection_reasons": [rejection_reason] if rejection_reason else [],
    }
    if row is not None:
        result["score"] = _confidence(row.adjusted_distance)
        result["raw_distance"] = row.distance
        result["adjusted_distance"] = row.adjusted_distance
    return result


def _match_outcome(outcomes: tuple[ProviderSearchOutcome, ...], rejection_reason: str | None) -> str:
    if rejection_reason:
        return "candidate_rejected"
    statuses = {item.status for item in outcomes}
    if statuses and statuses <= {"failed", "not_configured"}:
        return "provider_failure"
    return "zero_results"
