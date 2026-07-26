"""Matching API: candidate proposals + staging (docs/PLAN.md §9-10).

GET .../candidates is read-only (fetch + rank, stages nothing).
POST .../stage creates a match_proposal ChangeSet from one chosen
candidate — the same endpoint re-staging is just calling again with a
different (source, ref_id), per docs/PLAN.md's "PUT .../candidate"
semantics: it always rebuilds the edit set from scratch.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from muzilla.api.deps import get_provider_set, get_session
from muzilla.api.schemas.changesets import ChangeSetDetailOut
from muzilla.api.schemas.matching import MatchProposalOut, StageMatchRequest
from muzilla.services import changesets as changesets_service
from muzilla.services import matching as matching_service
from muzilla.services.providers import ProviderSet

router = APIRouter(tags=["matching"])


def _detail_or_500(session: Session, change_set_id: int) -> changesets_service.ChangeSetDetail:
    detail = changesets_service.get_changeset(session, change_set_id)
    assert detail is not None
    return detail


@router.get("/groups/{group_id}/candidates", response_model=MatchProposalOut)
async def get_group_candidates(
    group_id: int,
    session: Annotated[Session, Depends(get_session)],
    provider_set: Annotated[ProviderSet, Depends(get_provider_set)],
) -> matching_service.GroupMatchProposal:
    try:
        return await matching_service.propose_group_candidates(session, provider_set, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/groups/{group_id}/stage", response_model=ChangeSetDetailOut)
async def stage_group(
    group_id: int,
    body: StageMatchRequest,
    session: Annotated[Session, Depends(get_session)],
    provider_set: Annotated[ProviderSet, Depends(get_provider_set)],
) -> changesets_service.ChangeSetDetail:
    try:
        cs = await matching_service.stage_group_match(
            session, provider_set, group_id, source=body.source, ref_id=body.ref_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _detail_or_500(session, cs.id)


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
