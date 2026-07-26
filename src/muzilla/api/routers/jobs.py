"""Jobs API: list/inspect/cancel background work (docs/PLAN.md §10).

GET .../events (SSE) lands alongside the rest of the import-session
endpoints — this router covers the plain CRUD surface needed for
apply/undo's job-queue-backed flow to be pollable end-to-end.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from muzilla.api.deps import get_session
from muzilla.api.schemas.jobs import JobDetailOut, JobPageOut
from muzilla.services import jobs as jobs_service

router = APIRouter(tags=["jobs"])


@router.get("/jobs", response_model=JobPageOut)
async def list_jobs(
    session: Annotated[Session, Depends(get_session)],
    state: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> jobs_service.JobPage:
    return jobs_service.list_jobs(session, state=state, cursor=cursor, limit=limit)


@router.get("/jobs/{job_id}", response_model=JobDetailOut)
async def get_job(
    job_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> jobs_service.JobDetail:
    detail = jobs_service.get_job(session, job_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="job not found")
    return detail


@router.post("/jobs/{job_id}/cancel", response_model=JobDetailOut)
async def cancel_job(
    job_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> jobs_service.JobDetail:
    try:
        return jobs_service.request_job_cancel(session, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
