"""Pydantic schemas for track candidate and legacy track-stage endpoints.

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
    rejection_reason: str | None = None


class MatchProposalOut(BaseModel):
    model_config = {"from_attributes": True}

    candidates: list[CandidateRowOut]
    # Settled bands: strong / ambiguous / reject
    strong: bool = False
    ambiguous: bool = False
    band: Literal["strong", "ambiguous", "reject"] = "reject"
    # Legacy aliases — kept for compatibility
    auto_applicable: bool = False
    needs_confirmation: bool = False
    provider_outcomes: list[ProviderSearchOutcomeOut]
    rejection_reason: str | None


class StageMatchRequest(BaseModel):
    source: str
    ref_id: str
