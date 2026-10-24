"""Service-facing matching orchestration for API and worker callers."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.config.schema import PathsConfig
from muzilla.jobs.sync import run_sync
from muzilla.pipeline.matching import (
    CandidateRow,
    GroupMatchProposal,
    TrackMatchProposal,
    propose_group_candidates,
    propose_track_candidates,
    stage_group_match,
    stage_track_match,
)
from muzilla.pipeline.matching import (
    compose_track_candidate_review as _compose_track_candidate_review,
)
from muzilla.providers.set import ProviderSet


async def compose_track_candidate_review(
    session: Session,
    provider_set: ProviderSet,
    *,
    track_id: int,
    source: str,
    ref_id: str,
    paths_config: PathsConfig,
    library_root: Path,
) -> object:
    """Capture a new preview's physical source off the event loop before composing."""
    from muzilla.changes.writer import capture_file_guard
    from muzilla.db.models import ProposalRevision, Track
    from muzilla.pipeline.proposals import ProposalComposer, is_inert_initial_review

    track = session.get(Track, track_id)
    if track is None:
        raise ValueError(f"track {track_id} not found")
    bundle = ProposalComposer.open_bundle_for_scope(session, scope_type="track", scope_id=track_id)
    has_current_revision = False
    if bundle is not None:
        has_current_revision = (
            session.scalar(
                select(ProposalRevision.id).where(
                    ProposalRevision.review_bundle_id == bundle.id,
                    ProposalRevision.is_current.is_(True),
                )
            )
            is not None
        )

    source_guards: dict[int, dict[str, object]] | None = None
    needs_initial_observation = not has_current_revision or (
        bundle is not None and is_inert_initial_review(session, bundle.id)
    )
    if needs_initial_observation:
        if track.missing_since is not None:
            raise ValueError("stale_source: track is marked missing; refresh the catalog first")
        try:
            guard = await run_sync(capture_file_guard, Path(track.path), library_root)
        except Exception as exc:
            raise ValueError("stale_source: source file could not be captured") from exc
        file_facts = guard.get("file")
        if (
            guard.get("path") != track.path
            or not isinstance(file_facts, dict)
            or file_facts.get("size") != track.size_bytes
            or file_facts.get("mtime_ns") != track.mtime_ns
        ):
            raise ValueError("stale_source: file changed since the last catalog scan")
        source_guards = {track.id: guard}

    return await _compose_track_candidate_review(
        session,
        provider_set,
        track_id=track_id,
        source=source,
        ref_id=ref_id,
        paths_config=paths_config,
        source_guards=source_guards,
    )


__all__ = [
    "CandidateRow",
    "GroupMatchProposal",
    "TrackMatchProposal",
    "compose_track_candidate_review",
    "propose_group_candidates",
    "propose_track_candidates",
    "stage_group_match",
    "stage_track_match",
]
