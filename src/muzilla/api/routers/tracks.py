"""GET /api/tracks (cursor pagination, FTS search) and /api/tracks/{id}."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from muzilla.api.deps import get_session
from muzilla.api.schemas.tracks import TrackDetailOut, TrackFacetsOut, TrackPageOut
from muzilla.services import catalog

router = APIRouter(tags=["tracks"])

_VALID_FLAGS = {"missing-art", "unmatched", "errored"}


def _parse_flags(flags: str | None) -> tuple[str, ...]:
    if not flags:
        return ()
    return tuple(f for f in flags.split(",") if f in _VALID_FLAGS)


@router.get("/tracks", response_model=TrackPageOut)
async def list_tracks(
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    sort: str = "title",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    artist: str | None = None,
    album: str | None = None,
    genre: str | None = None,
    format: str | None = None,
    flags: str | None = None,
) -> catalog.TrackPage:
    return catalog.browse_tracks(
        session,
        q=q,
        sort=sort,
        cursor=cursor,
        limit=limit,
        artist=artist,
        album=album,
        genre=genre,
        format=format,
        flags=_parse_flags(flags),
    )


@router.get("/tracks/facets", response_model=TrackFacetsOut)
async def get_track_facets(
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
) -> catalog.TrackFacets:
    # Registered before /tracks/{track_id} — FastAPI resolves routes in
    # declaration order, and "facets" would otherwise be captured as
    # track_id (a 422, not silently wrong, but this is the correct fix
    # rather than relying on FastAPI's validation to catch it).
    return catalog.get_track_facets(session, q=q)


@router.get("/tracks/{track_id}", response_model=TrackDetailOut)
async def get_track(
    track_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> catalog.TrackDetail:
    detail = catalog.get_track_detail(session, track_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="track not found")
    return detail
