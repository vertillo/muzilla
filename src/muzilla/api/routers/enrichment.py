"""Enrichment API: on-demand ReplayGain/art/lyrics jobs (docs/PLAN.md
§Phase-6). Each endpoint is a thin one-off job enqueue, same shape as
POST /api/scan (api/routers/imports.py) — 202 + job_id, poll or SSE for
the outcome.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from muzilla.api.deps import get_session
from muzilla.api.schemas.jobs import JobEnqueuedOut
from muzilla.services import jobs as jobs_service

router = APIRouter(tags=["enrichment"])


@router.post("/enrich/replaygain", response_model=JobEnqueuedOut, status_code=202)
async def enrich_replaygain(
    session: Annotated[Session, Depends(get_session)],
) -> JobEnqueuedOut:
    summary = jobs_service.enqueue_replaygain(session)
    return JobEnqueuedOut(job_id=summary.id)


@router.post("/enrich/art", response_model=JobEnqueuedOut, status_code=202)
async def enrich_art(
    session: Annotated[Session, Depends(get_session)],
) -> JobEnqueuedOut:
    summary = jobs_service.enqueue_art(session)
    return JobEnqueuedOut(job_id=summary.id)


@router.post("/enrich/lyrics", response_model=JobEnqueuedOut, status_code=202)
async def enrich_lyrics(
    session: Annotated[Session, Depends(get_session)],
) -> JobEnqueuedOut:
    summary = jobs_service.enqueue_lyrics(session)
    return JobEnqueuedOut(job_id=summary.id)
