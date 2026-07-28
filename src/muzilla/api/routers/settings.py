"""Settings API: providers/tokens, filename templates, strip rules
(docs/PLAN.md §9, Phase 7 suggestion #3)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from muzilla.api.deps import get_session
from muzilla.api.schemas.settings import (
    ProviderSettingOut,
    SettingsSummaryOut,
    TemplatePreviewOut,
    TemplatePreviewRequest,
    TemplateSettingsOut,
    UpdateProviderSettingRequest,
    UpdateStripFieldsRequest,
    UpdateTemplatesRequest,
)
from muzilla.services import settings as settings_service

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsSummaryOut)
async def get_settings(session: Annotated[Session, Depends(get_session)]) -> settings_service.SettingsSummary:
    return settings_service.get_settings(session)


@router.put("/settings/providers/{provider}", response_model=ProviderSettingOut)
async def update_provider_setting(
    provider: str,
    body: UpdateProviderSettingRequest,
    session: Annotated[Session, Depends(get_session)],
) -> settings_service.ProviderSetting:
    try:
        return settings_service.update_provider_setting(
            session, provider=provider, enabled=body.enabled, token=body.token
        )
    except settings_service.SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/settings/templates", response_model=TemplateSettingsOut)
async def update_templates(
    body: UpdateTemplatesRequest,
    session: Annotated[Session, Depends(get_session)],
) -> settings_service.TemplateSettings:
    return settings_service.update_templates(
        session, album=body.album, singleton=body.singleton, default=body.default
    )


@router.put("/settings/strip-fields", response_model=list[str])
async def update_strip_fields(
    body: UpdateStripFieldsRequest,
    session: Annotated[Session, Depends(get_session)],
) -> list[str]:
    try:
        return settings_service.update_strip_fields(session, fields=body.fields)
    except settings_service.SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/settings/templates/preview", response_model=TemplatePreviewOut)
async def preview_template(body: TemplatePreviewRequest) -> settings_service.TemplatePreviewResult:
    # No session dependency: preview_template is deliberately DB-free
    # (renders against sample data only, per services/settings.py's
    # docstring) so a template author can iterate without a real track.
    return settings_service.preview_template(body.template)
