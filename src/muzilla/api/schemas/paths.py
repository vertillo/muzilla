"""Pydantic schemas for /api/paths.

The preview and rename requests support either one group or an arbitrary track
set, matching the service signatures. The rename endpoint stages a DRAFT
ChangeSet for review, like the other track mutation endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel


class PathPreviewRequest(BaseModel):
    track_ids: list[int] | None = None
    template: str | None = None
    """Overrides the configured album/singleton/query template for this
    preview only -- does not persist. None uses the configured template
    (config/schema.py's PathsConfig), same as stage_rename's default."""


class PathPreviewRowOut(BaseModel):
    track_id: int
    old_path: str
    new_path: str
    errors: tuple[str, ...]
    is_collision: bool
    conflicting_track_ids: tuple[int, ...] = ()
    collision_path: str | None = None


class PathPreviewOut(BaseModel):
    rows: list[PathPreviewRowOut]


class PathRenameRequest(BaseModel):
    track_ids: list[int] | None = None
    template: str | None = None
