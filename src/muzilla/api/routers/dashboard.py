"""GET /api/dashboard/summary — cheap library counts for the Dashboard
screen."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from muzilla.api.deps import get_session
from muzilla.api.schemas.dashboard import DashboardSummaryOut
from muzilla.services import dashboard as dashboard_service

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary", response_model=DashboardSummaryOut)
async def get_dashboard_summary(
    session: Annotated[Session, Depends(get_session)],
) -> dashboard_service.DashboardSummary:
    return dashboard_service.get_dashboard_summary(session)
