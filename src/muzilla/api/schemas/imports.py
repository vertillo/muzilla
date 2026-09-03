"""Pydantic schemas for /api/imports and POST /api/scan.

Mirrors services.imports's dataclasses field-for-field, same
convention as api/schemas/changesets.py and api/schemas/matching.py.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class StartImportRequest(BaseModel):
    library_root: str


class ImportConfigOut(BaseModel):
    """Read-only import root configuration and whether that root exists."""

    library_root: str
    library_root_exists: bool


class ImportTaskOut(BaseModel):
    model_config = {"from_attributes": True}

    stage: str
    seq: int
    state: Literal["pending", "running", "done", "failed", "skipped", "cancelled"]
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
    review_bundle_ids: list[int]
    # Deprecated compatibility field. New import UI must link only ReviewBundles.
    changeset_ids: list[int]


class ImportSessionPageOut(BaseModel):
    items: list[ImportSessionSummaryOut]


class ScanRequest(BaseModel):
    root: str


class BrowseEntryOut(BaseModel):
    name: str
    path: str
    kind: str
    is_symlink: bool = False
    symlink_target: str | None = None
    blocked: bool = False
    supported: bool | None = None
    ignored: bool = False


class BrowseOut(BaseModel):
    path: str
    parent: str | None
    entries: list[BrowseEntryOut]
    truncated: bool
    library_root: str


class PreviewRequest(BaseModel):
    path: str


class PreviewOut(BaseModel):
    scope_path: str
    scope_kind: str
    library_root: str
    supported_count: int
    unsupported_count: int
    ignored_sidecar_count: int
    excluded_dir_count: int
    symlink_excluded_count: int
    total_files_considered: int
    truncated: bool
    unsupported_examples: list[str]
    excluded_dir_examples: list[str]
