"""Jobs API: list/inspect/cancel background work, plus SSE progress
(docs/PLAN.md §9-10).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from muzilla.api.deps import get_config, get_session
from muzilla.api.schemas.jobs import JobDetailOut, JobPageOut
from muzilla.config.schema import Config
from muzilla.services import jobs as jobs_service
from muzilla.services.db import session_scope

router = APIRouter(tags=["jobs"])

_TERMINAL_STATES = ("succeeded", "failed", "cancelled")
_SSE_POLL_SECONDS = 0.25


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


async def _job_events_stream(config: Config, job_id: int, after: int) -> AsyncIterator[str]:
    """Replays events after `after`, then polls for new ones until the
    job reaches a terminal state (docs/PLAN.md §9: "GET .../events
    ?after=<seq> replays from job_events"). Opens a short-lived session
    per tick — matches the worker's own per-unit-of-work session
    pattern — rather than holding one open for the connection's life.
    """
    last_seq = after
    while True:
        with session_scope(config) as session:
            job = jobs_service.get_job(session, job_id)
            if job is None:
                yield 'event: error\ndata: {"detail": "job not found"}\n\n'
                return
            events = jobs_service.list_job_events(session, job_id, last_seq)

        for event in events:
            last_seq = event.seq
            payload = {"seq": event.seq, "kind": event.kind, "payload": event.payload}
            yield f"data: {json.dumps(payload)}\n\n"

        if job.state in _TERMINAL_STATES and not events:
            yield f'event: done\ndata: {{"state": "{job.state}"}}\n\n'
            return

        await asyncio.sleep(_SSE_POLL_SECONDS)


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: int,
    config: Annotated[Config, Depends(get_config)],
    after: int = 0,
) -> StreamingResponse:
    return StreamingResponse(
        _job_events_stream(config, job_id, after), media_type="text/event-stream"
    )
