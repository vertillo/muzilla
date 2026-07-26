"""Pydantic schemas for /api/groups/{id}/candidates, /api/groups/{id}/stage,
/api/tracks/{id}/candidates, /api/tracks/{id}/stage (docs/PLAN.md §10).

Mirrors services.matching's dataclasses field-for-field, same
convention as api/schemas/changesets.py.
"""

from __future__ import annotations

from pydantic import BaseModel


class CandidateRowOut(BaseModel):
    model_config = {"from_attributes": True}

    source: str
    ref_id: str
    album: str | None
    album_artist: str | None
    year: int | None
    label: str | None
    catalog_number: str | None
    track_count: int
    distance: float
    adjusted_distance: float
    is_duplicate_of: tuple[int, ...]
    corroborated_by: tuple[str, ...]


class MatchProposalOut(BaseModel):
    model_config = {"from_attributes": True}

    candidates: list[CandidateRowOut]
    auto_applicable: bool
    needs_confirmation: bool


class StageMatchRequest(BaseModel):
    source: str
    ref_id: str
