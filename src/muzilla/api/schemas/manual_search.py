"""OpenAPI contract for manual Matching v2 search inside a ReviewBundle."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from muzilla.api.schemas.matching import CandidateRowOut, ProviderSearchOutcomeOut
from muzilla.api.schemas.reviews import ReviewBundleDetailOut


class ManualCandidateSearchRequest(BaseModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    year: int | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    providers: list[str] = Field(default_factory=list)
    page: int = 0
    page_size: int = 10


class ManualSearchQueryOut(BaseModel):
    title: str | None
    artist: str | None
    album: str | None
    year: int | None
    duration_ms: int | None
    isrc: str | None
    providers: tuple[str, ...]
    page: int
    page_size: int


class ManualCandidateSearchOut(BaseModel):
    model_config = {"from_attributes": True}

    query: ManualSearchQueryOut
    candidates: tuple[CandidateRowOut, ...]
    provider_outcomes: tuple[ProviderSearchOutcomeOut, ...]
    has_more: bool


class ProviderSearchCapabilityOut(BaseModel):
    model_config = {"from_attributes": True}

    provider: str
    status: Literal["available", "not_configured"]
    supports_search: bool


class ManualCandidateImportRequest(BaseModel):
    source: str
    ref_id: str
    force: bool = False


class CandidateUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    force: bool = False


class CandidateUrlRefOut(BaseModel):
    model_config = {"from_attributes": True}

    provider: Literal["musicbrainz", "deezer", "discogs"]
    candidate_type: Literal["release", "album", "track"]
    provider_id: str


class CandidateUrlImportOut(BaseModel):
    model_config = {"from_attributes": True}

    candidate: CandidateUrlRefOut
    already_selected: bool
    review: ReviewBundleDetailOut
