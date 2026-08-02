"""Pydantic schemas for /api/groups/{id}/candidates, /api/groups/{id}/stage,
/api/tracks/{id}/candidates, /api/tracks/{id}/stage (docs/PLAN.md §10).

Mirrors services.matching's dataclasses field-for-field, same
convention as api/schemas/changesets.py.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ScoreSignalOut(BaseModel):
    field: str
    distance: float
    weight: float
    contribution: float


class ProviderSearchOutcomeOut(BaseModel):
    provider: str
    status: Literal["results", "zero_results", "failed", "not_configured"]
    result_count: int
    detail: str | None


class CandidateRowOut(BaseModel):
    model_config = {"from_attributes": True}

    source: str
    ref_id: str
    album: str | None
    album_artist: str | None
    year: int | None
    label: str | None
    catalog_number: str | None
    track_count: int | None
    candidate_type: str
    representative_title: str | None
    representative_artist: str | None
    representative_position: int | None
    representative_duration_ms: int | None
    cover_url: str | None
    distance: float
    adjusted_distance: float
    score_signals: tuple[ScoreSignalOut, ...]
    is_duplicate_of: tuple[int, ...]
    corroborated_by: tuple[str, ...]


class MatchProposalOut(BaseModel):
    model_config = {"from_attributes": True}

    candidates: list[CandidateRowOut]
    auto_applicable: bool
    needs_confirmation: bool
    provider_outcomes: list[ProviderSearchOutcomeOut]
    rejection_reason: str | None


class StageMatchRequest(BaseModel):
    source: str
    ref_id: str
