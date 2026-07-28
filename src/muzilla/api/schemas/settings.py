"""Pydantic models for GET/PATCH /api/settings/* (docs/PLAN.md §9)."""

from __future__ import annotations

from pydantic import BaseModel


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


class SettingsSummaryOut(BaseModel):
    providers: list[ProviderSettingOut]
    templates: TemplateSettingsOut
    strip_fields: list[str]


class UpdateProviderSettingRequest(BaseModel):
    enabled: bool | None = None
    token: str | None = None
    """Write-only. Omit to leave the stored token unchanged; empty
    string clears it; any other value replaces it."""


class UpdateTemplatesRequest(BaseModel):
    album: str | None = None
    singleton: str | None = None
    default: str | None = None


class UpdateStripFieldsRequest(BaseModel):
    fields: list[str]


class TemplatePreviewRequest(BaseModel):
    template: str


class TemplatePreviewOut(BaseModel):
    path: str
    errors: list[str]
