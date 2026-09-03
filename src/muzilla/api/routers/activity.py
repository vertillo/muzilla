"""Activity API: aggregated user-action view over jobs/sessions/runs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from muzilla.api.deps import get_session
from muzilla.api.schemas.activity import ActivityPageOut
from muzilla.services import activity as activity_service

router = APIRouter(tags=["activity"])


@router.get("/activity", response_model=ActivityPageOut)
async def list_activity(
    session: Annotated[Session, Depends(get_session)],
    cursor: str | None = None,
    limit: int = 50,
    include_system: bool = False,
) -> activity_service.ActivityPage:
    return activity_service.list_activity(
        session, cursor=cursor, limit=limit, include_system=include_system
    )
