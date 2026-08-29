"""Matching API: candidate proposals."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from muzilla.api.deps import get_config, get_provider_set, get_session
from muzilla.api.schemas.matching import MatchProposalOut, StageMatchRequest
from muzilla.api.schemas.reviews import ReviewBundleDetailOut
from muzilla.config.schema import Config
from muzilla.services import matching as matching_service
from muzilla.services import settings as settings_service
from muzilla.services.providers import ProviderSet

router = APIRouter(tags=["matching"])


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


@router.post("/tracks/{track_id}/review/candidate", response_model=ReviewBundleDetailOut)
async def choose_track_candidate_for_review(
    track_id: int,
    body: StageMatchRequest,
    session: Annotated[Session, Depends(get_session)],
    provider_set: Annotated[ProviderSet, Depends(get_provider_set)],
    config: Annotated[Config, Depends(get_config)],
) -> object:
    try:
        effective_paths = settings_service.effective_paths_config(session, config.paths)
        detail = await matching_service.compose_track_candidate_review(
            session,
            provider_set,
            track_id=track_id,
            source=body.source,
            ref_id=body.ref_id,
            paths_config=effective_paths,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()
    return detail
