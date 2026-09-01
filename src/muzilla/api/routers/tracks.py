"""GET /api/tracks (cursor pagination, FTS search) and /api/tracks/{id}."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from muzilla.api.deps import get_session
from muzilla.api.schemas.jobs import JobEnqueuedOut
from muzilla.api.schemas.reviews import ReviewBundleDetailOut
from muzilla.api.schemas.tracks import TrackDetailOut, TrackFacetsOut, TrackPageOut
from muzilla.services import catalog
from muzilla.services import grouping_resolver as grouping_resolver_service
from muzilla.services import jobs as jobs_service
from muzilla.services import proposals as proposals_service


class TrackPatchRequest(BaseModel):
    fields: dict[str, object]


router = APIRouter(tags=["tracks"])

_VALID_FLAGS = {"missing", "missing-art", "unmatched", "errored"}


def _parse_flags(flags: str | None) -> tuple[str, ...]:
    if not flags:
        return ()
    return tuple(f for f in flags.split(",") if f in _VALID_FLAGS)


@router.get("/tracks", response_model=TrackPageOut)
async def list_tracks(
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    sort: str = "title",
    direction: str = "asc",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    artist: str | None = None,
    album: str | None = None,
    genre: str | None = None,
    format: str | None = None,
    flags: str | None = None,
) -> catalog.TrackPage:
    try:
        return catalog.browse_tracks(
            session,
            q=q,
            sort=sort,
            direction=direction,
            cursor=cursor,
            limit=limit,
            artist=artist,
            album=album,
            genre=genre,
            format=format,
            flags=_parse_flags(flags),
        )
    except catalog.CatalogSearchUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "1"}) from exc


@router.get("/tracks/facets", response_model=TrackFacetsOut)
async def get_track_facets(
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    genre: str | None = None,
    format: str | None = None,
    flags: str | None = None,
    facet_q: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    cursor: str | None = None,
) -> catalog.TrackFacets:
    # Registered before /tracks/{track_id} — FastAPI resolves routes in
    # declaration order, and "facets" would otherwise be captured as
    # track_id (a 422, not silently wrong, but this is the correct fix
    # rather than relying on FastAPI's validation to catch it).
    try:
        return catalog.get_track_facets(
            session,
            q=q,
            artist=artist,
            album=album,
            genre=genre,
            format=format,
            flags=_parse_flags(flags),
            facet_q=facet_q,
            limit=limit,
            cursor=cursor,
        )
    except catalog.CatalogSearchUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "1"}) from exc


@router.post("/tracks/{track_id}/rescan", response_model=JobEnqueuedOut, status_code=202)
async def rescan_track(
    track_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> JobEnqueuedOut:
    try:
        job = jobs_service.enqueue_track_rescan(session, track_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JobEnqueuedOut(job_id=job.id)


@router.post("/tracks/{track_id}/review/manual", response_model=ReviewBundleDetailOut)
async def create_manual_track_review(
    track_id: int,
    body: TrackPatchRequest,
    session: Annotated[Session, Depends(get_session)],
) -> object:
    try:
        detail = proposals_service.ProposalComposer(session).compose_manual_track_edit(
            track_id=track_id, field_values=body.fields
        )
    except proposals_service.ProposalCompositionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return detail


@router.post("/tracks/{track_id}/review/grouping", response_model=ReviewBundleDetailOut)
async def create_grouping_review(
    track_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> object:
    """Offer constrained collection corrections as a review, never as a direct move."""
    try:
        detail = grouping_resolver_service.create_grouping_review(session, track_id)
    except grouping_resolver_service.GroupingResolverError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return detail


@router.get("/tracks/{track_id}", response_model=TrackDetailOut)
async def get_track(
    track_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> catalog.TrackDetail:
    detail = catalog.get_track_detail(session, track_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="track not found")
    return detail
