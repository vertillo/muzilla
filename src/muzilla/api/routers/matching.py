"""Matching API: candidate proposals + staging.

GET .../candidates is read-only (fetch + rank, stages nothing).
POST .../stage creates a match_proposal ChangeSet from one chosen
candidate — calling the same endpoint again with a different (source, ref_id)
always rebuilds the edit set from scratch.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from muzilla.api.deps import get_config, get_provider_set, get_session
from muzilla.api.schemas.changesets import ChangeSetDetailOut
from muzilla.api.schemas.matching import MatchProposalOut, StageMatchRequest
from muzilla.api.schemas.reviews import ReviewBundleDetailOut
from muzilla.config.schema import Config
from muzilla.services import changesets as changesets_service
from muzilla.services import matching as matching_service
from muzilla.services.providers import ProviderSet

router = APIRouter(tags=["matching"])


def _detail_or_500(session: Session, change_set_id: int) -> changesets_service.ChangeSetDetail:
    detail = changesets_service.get_changeset(session, change_set_id)
    assert detail is not None
    return detail


@router.get("/tracks/{track_id}/candidates", response_model=MatchProposalOut)
async def get_track_candidates(
    track_id: int,
    session: Annotated[Session, Depends(get_session)],
    provider_set: Annotated[ProviderSet, Depends(get_provider_set)],
) -> matching_service.TrackMatchProposal:
    try:
        return await matching_service.propose_track_candidates(session, provider_set, track_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tracks/{track_id}/stage", response_model=ChangeSetDetailOut)
async def stage_track(
    track_id: int,
    body: StageMatchRequest,
    session: Annotated[Session, Depends(get_session)],
    provider_set: Annotated[ProviderSet, Depends(get_provider_set)],
) -> changesets_service.ChangeSetDetail:
    try:
        cs = await matching_service.stage_track_match(
            session, provider_set, track_id, source=body.source, ref_id=body.ref_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _detail_or_500(session, cs.id)


@router.post("/tracks/{track_id}/review/candidate", response_model=ReviewBundleDetailOut)
async def choose_track_candidate_for_review(
    track_id: int,
    body: StageMatchRequest,
    session: Annotated[Session, Depends(get_session)],
    provider_set: Annotated[ProviderSet, Depends(get_provider_set)],
    config: Annotated[Config, Depends(get_config)],
) -> object:
    """Hydrate a selected candidate into the track's one active review.

    The ``/stage`` route remains a compatibility adapter; new catalog
    work always lands in ReviewBundle before any apply can run.
    """
    try:
        detail = await matching_service.compose_track_candidate_review(
            session,
            provider_set,
            track_id=track_id,
            source=body.source,
            ref_id=body.ref_id,
            paths_config=config.paths,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()
    return detail
