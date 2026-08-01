"""Enrichment API: on-demand ReplayGain/art/lyrics jobs (docs/PLAN.md
§Phase-6). Each endpoint is a thin one-off job enqueue, same shape as
POST /api/scan (api/routers/imports.py) — 202 + job_id, poll or SSE for
the outcome.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from muzilla.api.deps import get_config, get_runtime_capability_cache, get_session
from muzilla.api.schemas.jobs import JobEnqueuedOut
from muzilla.config.schema import Config
from muzilla.services import capabilities as capabilities_service
from muzilla.services import jobs as jobs_service

router = APIRouter(tags=["enrichment"])


@router.post("/enrich/replaygain", response_model=JobEnqueuedOut, status_code=202)
async def enrich_replaygain(
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[Config, Depends(get_config)],
    cache: Annotated[
        capabilities_service.RuntimeCapabilityCache, Depends(get_runtime_capability_cache)
    ],
) -> JobEnqueuedOut:
    runtime = await cache.get(config)
    capability = runtime.replaygain
    if not capability.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"ReplayGain unavailable: {capability.detail}",
        )
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


@router.post("/enrich/lyrics/{job_id}/retry-failed", response_model=JobEnqueuedOut, status_code=202)
async def retry_failed_lyrics(
    job_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> JobEnqueuedOut:
    try:
        summary = jobs_service.retry_failed_lyrics(session, job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JobEnqueuedOut(job_id=summary.id)
