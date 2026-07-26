"""Pydantic schemas for /api/jobs (docs/PLAN.md §10).

Mirrors services.jobs's dataclasses field-for-field, same convention
as api/schemas/changesets.py and api/schemas/matching.py.
"""

from __future__ import annotations

from pydantic import BaseModel


class JobSummaryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    type: str
    state: str
    priority: int
    progress_current: int
    progress_total: int | None
    progress_message: str | None
    attempts: int
    error: str | None


class JobDetailOut(JobSummaryOut):
    payload: dict[str, object]
    result: dict[str, object] | None


class JobPageOut(BaseModel):
    model_config = {"from_attributes": True}

    items: list[JobSummaryOut]
    next_cursor: str | None
