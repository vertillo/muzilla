"""Pydantic schemas for /api/imports and POST /api/scan (docs/PLAN.md §10).

Mirrors services.imports's dataclasses field-for-field, same
convention as api/schemas/changesets.py and api/schemas/matching.py.
"""

from __future__ import annotations

from pydantic import BaseModel


class StartImportRequest(BaseModel):
    library_root: str


class ImportTaskOut(BaseModel):
    model_config = {"from_attributes": True}

    stage: str
    seq: int
    state: str
    error: str | None


class ImportSessionSummaryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    library_root: str
    state: str
    job_id: int | None
    stats: dict[str, object]
    error: str | None


class ImportSessionDetailOut(ImportSessionSummaryOut):
    tasks: list[ImportTaskOut]
    changeset_ids: list[int]


class ScanRequest(BaseModel):
    root: str
