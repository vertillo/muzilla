"""Schemas for Activity aggregation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ActivityItemOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    kind: Literal["import", "scan", "apply", "undo", "duplicate_analysis"]
    title: str
    state: str
    created_at: datetime
    updated_at: datetime
    job_id: int | None
    import_session_id: int | None
    review_bundle_id: int | None
    apply_run_id: int | None
    undo_run_id: int | None
    progress_current: int | None
    progress_total: int | None
    progress_message: str | None
    error: str | None
    result: dict[str, object] | None
    cancellable: bool


class ActivityPageOut(BaseModel):
    model_config = {"from_attributes": True}

    items: list[ActivityItemOut]
    next_cursor: str | None
