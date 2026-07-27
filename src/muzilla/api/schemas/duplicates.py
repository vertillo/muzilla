"""Pydantic schemas for /api/duplicates (docs/PLAN.md §Phase-6)."""

from __future__ import annotations

from pydantic import BaseModel


class DuplicateTrackOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    path: str
    title: str | None
    artist: str | None
    format: str | None
    bitrate: int | None
    duration_ms: int | None


class DuplicateGroupOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    mb_recording_id: str
    basis: str
    dismissed: bool
    tracks: list[DuplicateTrackOut]


class DuplicateGroupListOut(BaseModel):
    items: list[DuplicateGroupOut]
