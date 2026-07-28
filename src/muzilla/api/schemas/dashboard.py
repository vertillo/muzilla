"""Pydantic response model for GET /api/dashboard/summary."""

from __future__ import annotations

from pydantic import BaseModel


class DashboardSummaryOut(BaseModel):
    model_config = {"from_attributes": True}

    total_tracks: int
    tracks_missing: int
    tracks_with_errors: int
    tracks_missing_art: int
    album_count: int
    singleton_count: int
    ungrouped_track_count: int
