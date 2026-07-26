"""GET /api/tracks (cursor pagination, FTS search) and /api/tracks/{id}."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from muzilla.api.deps import get_session
from muzilla.api.schemas.tracks import TrackDetailOut, TrackPageOut
from muzilla.services import catalog

router = APIRouter(tags=["tracks"])


@router.get("/tracks", response_model=TrackPageOut)
async def list_tracks(
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    sort: str = "title",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> catalog.TrackPage:
    return catalog.browse_tracks(session, q=q, sort=sort, cursor=cursor, limit=limit)


@router.get("/tracks/{track_id}", response_model=TrackDetailOut)
async def get_track(
    track_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> catalog.TrackDetail:
    detail = catalog.get_track_detail(session, track_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="track not found")
    return detail
