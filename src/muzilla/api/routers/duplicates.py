"""Duplicate-detection API: list/dismiss fingerprint-based duplicate
groups, and trigger detection on demand (docs/product-spec.md).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from muzilla.api.deps import get_session
from muzilla.api.schemas.duplicates import DuplicateGroupListOut, DuplicateGroupOut
from muzilla.api.schemas.jobs import JobEnqueuedOut
from muzilla.services import duplicates as duplicates_service
from muzilla.services import jobs as jobs_service

router = APIRouter(tags=["duplicates"])


@router.get("/duplicates", response_model=DuplicateGroupListOut)
async def list_duplicates(
    session: Annotated[Session, Depends(get_session)],
    include_dismissed: bool = False,
) -> DuplicateGroupListOut:
    items = duplicates_service.list_duplicate_groups(session, include_dismissed=include_dismissed)
    return DuplicateGroupListOut(items=items)  # type: ignore[arg-type]


@router.post("/duplicates/{group_id}/dismiss", response_model=DuplicateGroupOut)
async def dismiss_duplicate(
    group_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> duplicates_service.DuplicateGroupOut:
    try:
        result = duplicates_service.dismiss_duplicate_group(session, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()
    return result


@router.post("/duplicates/detect", response_model=JobEnqueuedOut, status_code=202)
async def detect_duplicates(
    session: Annotated[Session, Depends(get_session)],
) -> JobEnqueuedOut:
    summary = jobs_service.enqueue_duplicate_detection(session)
    return JobEnqueuedOut(job_id=summary.id)
