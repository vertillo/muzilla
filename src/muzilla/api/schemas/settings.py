"""Pydantic models for GET/PATCH /api/settings/*."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, SecretStr


class ProviderSettingOut(BaseModel):
    model_config = {"from_attributes": True}

    provider: str
    enabled: bool
    token_configured: bool
    """Never the token value itself — write-only from the client's
    perspective, matching MUZILLA_AUTH__PASSWORD's SecretStr treatment
    elsewhere in this codebase."""


class TemplateSettingsOut(BaseModel):
    model_config = {"from_attributes": True}

    album: str | None
    singleton: str | None
    default: str | None


class EnrichmentSettingsOut(BaseModel):
    model_config = {"from_attributes": True}

    metadata_auto: bool
    art_auto: bool
    lyrics_auto: bool
    replaygain_auto: bool


class PathsPolicyOut(BaseModel):
    model_config = {"from_attributes": True}

    create_directories: bool


class SettingsSummaryOut(BaseModel):
    providers: list[ProviderSettingOut]
    templates: TemplateSettingsOut
    strip_fields: list[str]
    enrichment: EnrichmentSettingsOut
    paths_policy: PathsPolicyOut


class UpdateProviderSettingRequest(BaseModel):
    enabled: bool | None = None
    token: str | None = None
    """Write-only. Omit to leave the stored token unchanged; empty
    string clears it; any other value replaces it."""


class UpdateTemplatesRequest(BaseModel):
    album: str | None = None
    singleton: str | None = None
    default: str | None = None


class UpdateEnrichmentRequest(BaseModel):
    metadata_auto: bool | None = None
    art_auto: bool | None = None
    lyrics_auto: bool | None = None
    replaygain_auto: bool | None = None


class UpdatePathsPolicyRequest(BaseModel):
    create_directories: bool | None = None


class UpdateStripFieldsRequest(BaseModel):
    fields: list[str]


class TemplatePreviewRequest(BaseModel):
    template: str


class TemplatePreviewOut(BaseModel):
    path: str
    errors: list[str]


class CatalogResetRequest(BaseModel):
    scope: Literal["catalog_and_activity"]
    confirmation: Literal["RESET CATALOG AND ACTIVITY"]


class FactoryResetRequest(BaseModel):
    scope: Literal["factory"]
    confirmation: Literal["FACTORY RESET MUZILLA"]
    password: SecretStr


class ResetResultOut(BaseModel):
    model_config = {"from_attributes": True}

    operation_id: int
    scope: str
    state: str
    settings_preserved: bool
    secrets_preserved: bool
    music_files_touched: bool
    deleted_counts: dict[str, int]
