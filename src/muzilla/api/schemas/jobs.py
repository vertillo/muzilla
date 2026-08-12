"""Pydantic schemas for /api/jobs (docs/product-spec.md).

Mirrors services.jobs's dataclasses field-for-field, same convention
as api/schemas/changesets.py and api/schemas/matching.py.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class JobSummaryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    type: str
    state: Literal["pending", "running", "cancelling", "succeeded", "failed", "cancelled"]
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


class JobEnqueuedOut(BaseModel):
    """POST .../apply, .../undo, /api/scan, and /api/imports all return
    this — docs/product-spec.md: `POST .../apply -> 202 {job_id}`. Poll
    GET /api/jobs/{id} or subscribe to GET /api/jobs/{id}/events for
    the outcome."""

    job_id: int
