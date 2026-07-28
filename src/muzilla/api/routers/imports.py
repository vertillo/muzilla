"""Import session API: start/inspect a resumable whole-library import,
and the ad-hoc scan endpoint (docs/PLAN.md §10).

POST /api/scan is a thin one-off job enqueue (services.jobs.enqueue_scan)
distinct from POST /api/imports, which additionally runs fingerprint/
group/match via the `import` orchestrator job.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from muzilla.api.deps import get_config, get_session
from muzilla.api.schemas.imports import (
    ImportSessionDetailOut,
    ImportSessionSummaryOut,
    ScanRequest,
    StartImportRequest,
)
from muzilla.api.schemas.jobs import JobEnqueuedOut
from muzilla.config.schema import Config
from muzilla.services import imports as imports_service
from muzilla.services import jobs as jobs_service
from muzilla.services.paths_guard import require_within_library_root

router = APIRouter(tags=["imports"])


@router.post("/scan", response_model=JobEnqueuedOut, status_code=202)
async def scan(
    body: ScanRequest,
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[Config, Depends(get_config)],
) -> JobEnqueuedOut:
    try:
        require_within_library_root(body.root, library_root=config.storage.library_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = jobs_service.enqueue_scan(session, body.root)
    return JobEnqueuedOut(job_id=summary.id)


@router.post("/imports", response_model=ImportSessionSummaryOut, status_code=202)
async def start_import(
    body: StartImportRequest,
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[Config, Depends(get_config)],
) -> imports_service.ImportSessionSummary:
    try:
        require_within_library_root(
            body.library_root, library_root=config.storage.library_root
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return imports_service.start_import(session, body.library_root)


@router.get("/imports/{import_session_id}", response_model=ImportSessionDetailOut)
async def get_import(
    import_session_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> imports_service.ImportSessionDetail:
    detail = imports_service.get_import_session(session, import_session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="import session not found")
    return detail


@router.post("/imports/{import_session_id}/resume", response_model=ImportSessionSummaryOut)
async def resume_import(
    import_session_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> imports_service.ImportSessionSummary:
    try:
        return imports_service.resume_import(session, import_session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
