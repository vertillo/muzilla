"""Settings API: providers/tokens, filename templates, strip rules
(docs/PLAN.md §9, Phase 7 suggestion #3)."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from muzilla.api.deps import (
    get_effective_provider_config,
    get_provider_runtime,
    get_secret_store,
    get_session,
)
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
from muzilla.config.schema import Config
from muzilla.services import providers as providers_service
from muzilla.services import settings as settings_service
from muzilla.services.secrets import SecretStore, SecretStoreError

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsSummaryOut)
async def get_settings(
    session: Annotated[Session, Depends(get_session)],
    effective_config: Annotated[Config, Depends(get_effective_provider_config)],
) -> settings_service.SettingsSummary:
    return settings_service.get_settings(session, provider_config=effective_config)


@router.put("/settings/providers/{provider}", response_model=ProviderSettingOut)
async def update_provider_setting(
    provider: str,
    request: Request,
    body: UpdateProviderSettingRequest,
    session: Annotated[Session, Depends(get_session)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
    provider_runtime: Annotated[providers_service.ProviderSetRuntime, Depends(get_provider_runtime)],
) -> settings_service.ProviderSetting:
    try:
        settings_service.update_provider_setting(
            session,
            secret_store=secret_store,
            provider=provider,
            enabled=body.enabled,
            token=body.token,
        )
        resolver: providers_service.EffectiveConfigResolver = request.app.state.provider_config_resolver
        effective_config = resolver.resolve(session)
        replacement = providers_service.build_provider_set(effective_config)
        await provider_runtime.swap(replacement, effective_config)
        _schedule_provider_checks(request, provider_runtime)
        return settings_service.provider_setting_from_effective_config(provider, effective_config)
    except settings_service.SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SecretStoreError as exc:
        raise HTTPException(status_code=503, detail="provider credentials could not be loaded") from exc


def _schedule_provider_checks(request: Request, provider_runtime: providers_service.ProviderSetRuntime) -> None:
    task = asyncio.create_task(providers_service.check_all_provider_connections(provider_runtime))
    request.app.state.provider_health_tasks.add(task)
    task.add_done_callback(request.app.state.provider_health_tasks.discard)


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
