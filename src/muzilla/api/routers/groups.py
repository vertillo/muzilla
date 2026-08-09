"""Grouping workspace API: GET /api/groups, /api/groups/{id}, and the
correction actions (merge/split/reassign/pin/force-singleton). Each
stages a grouping_correction ChangeSet and applies it immediately
(services/grouping.py's auto-apply — Phase 7 item 6, docs/KNOWN_BUGS.md
#3's fix) before returning, so the response's `state` reflects real
applied/failed status, not a still-draft changeset the caller would
otherwise have to separately apply. The explicit session.commit() calls
below are redundant with apply_now()'s own commit inside each service
call but kept for clarity/safety rather than relying on that being true
forever.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from muzilla.api.deps import get_session
from muzilla.api.schemas.changesets import ChangeSetDetailOut
from muzilla.api.schemas.groups import (
    ForceSingletonRequest,
    GroupDetailOut,
    GroupListOut,
    MergeGroupsRequest,
    RunCascadeResultOut,
    SplitGroupRequest,
)
from muzilla.services import changesets as changesets_service
from muzilla.services import grouping as grouping_service

router = APIRouter(tags=["groups"])


@router.get("/groups", response_model=GroupListOut)
async def list_groups(session: Annotated[Session, Depends(get_session)]) -> GroupListOut:
    return GroupListOut(items=grouping_service.list_groups(session))  # type: ignore[arg-type]


@router.get("/groups/{group_id}", response_model=GroupDetailOut)
async def get_group(
    group_id: int, session: Annotated[Session, Depends(get_session)]
) -> grouping_service.GroupDetail:
    detail = grouping_service.get_group(session, group_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="group not found")
    return detail


@router.post("/groups/cascade", response_model=RunCascadeResultOut)
async def run_cascade(session: Annotated[Session, Depends(get_session)]) -> RunCascadeResultOut:
    result = grouping_service.run_cascade(session)
    return RunCascadeResultOut(
        groups_created=result.groups_created,
        groups_updated=result.groups_updated,
        tracks_grouped=result.tracks_grouped,
        tracks_skipped_pinned=result.tracks_skipped_pinned,
    )


def _detail_or_500(session: Session, change_set_id: int) -> changesets_service.ChangeSetDetail:
    detail = changesets_service.get_changeset(session, change_set_id)
    assert detail is not None
    return detail


@router.post("/groups/{group_id}/merge", response_model=ChangeSetDetailOut)
async def merge_groups(
    group_id: int,
    body: MergeGroupsRequest,
    session: Annotated[Session, Depends(get_session)],
) -> changesets_service.ChangeSetDetail:
    try:
        cs = grouping_service.merge_groups(
            session, into_group_id=group_id, from_group_ids=body.from_group_ids
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _detail_or_500(session, cs.id)


@router.post("/groups/{group_id}/split", response_model=ChangeSetDetailOut)
async def split_group(
    group_id: int,
    body: SplitGroupRequest,
    session: Annotated[Session, Depends(get_session)],
) -> changesets_service.ChangeSetDetail:
    try:
        cs = grouping_service.split_group(session, group_id=group_id, track_ids=body.track_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _detail_or_500(session, cs.id)



@router.post("/groups/force-singleton", response_model=ChangeSetDetailOut)
async def force_to_singleton(
    body: ForceSingletonRequest,
    session: Annotated[Session, Depends(get_session)],
) -> changesets_service.ChangeSetDetail:
    try:
        cs = grouping_service.force_to_singleton(session, track_id=body.track_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _detail_or_500(session, cs.id)


@router.post("/groups/{group_id}/pin", response_model=ChangeSetDetailOut)
async def pin_group(
    group_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> changesets_service.ChangeSetDetail:
    try:
        cs = grouping_service.pin_group(session, group_id=group_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _detail_or_500(session, cs.id)
