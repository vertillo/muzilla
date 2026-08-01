"""Pydantic schemas for /api/imports and POST /api/scan (docs/PLAN.md §10).

Mirrors services.imports's dataclasses field-for-field, same
convention as api/schemas/changesets.py and api/schemas/matching.py.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class StartImportRequest(BaseModel):
    library_root: str


class ImportConfigOut(BaseModel):
    """docs/PLAN.md §12e step 6.5 item 4: ImportWizard's free-text path
    input became wrong once step 2.7 constrained scan/import roots to
    storage.library_root or a descendant — this is what the wizard
    reads to show the configured root read-only instead.

    library_root itself always has a value (StorageConfig defaults it
    to /music), so "unset" in practice means the directory doesn't
    exist on disk yet — library_root_exists carries that instead of
    making the path itself optional."""

    library_root: str
    library_root_exists: bool


class ImportTaskOut(BaseModel):
    model_config = {"from_attributes": True}

    stage: str
    seq: int
    state: Literal["pending", "running", "done", "failed", "skipped"]
    error: str | None


class ImportSessionSummaryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    library_root: str
    state: Literal[
        "pending",
        "scanning",
        "fingerprinting",
        "grouping",
        "matching",
        "reviewing",
        "completed",
        "failed",
        "cancelled",
    ]
    job_id: int | None
    stats: dict[str, object]
    error: str | None


class ImportSessionDetailOut(ImportSessionSummaryOut):
    tasks: list[ImportTaskOut]
    changeset_ids: list[int]


class ScanRequest(BaseModel):
    root: str
