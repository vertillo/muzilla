"""Settings API: providers/tokens, filename templates, strip rules."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from muzilla.api.deps import (
    SESSION_COOKIE_NAME,
    get_config,
    get_effective_provider_config,
    get_provider_runtime,
    get_secret_store,
    get_session,
)
from muzilla.api.middleware import MutationGate, MutationGateBusy
from muzilla.api.schemas.settings import (
    CatalogResetRequest,
    EnrichmentSettingsOut,
    FactoryResetRequest,
    PathsPolicyOut,
    ProviderSettingOut,
    ResetResultOut,
    SettingsSummaryOut,
    TemplatePreviewOut,
    TemplatePreviewRequest,
    TemplateSettingsOut,
    UpdateEnrichmentRequest,
    UpdatePathsPolicyRequest,
    UpdateProviderSettingRequest,
    UpdateStripFieldsRequest,
    UpdateTemplatesRequest,
)
from muzilla.api.security import require_sensitive_mutation
from muzilla.config.schema import Config
from muzilla.services import auth as auth_service
from muzilla.services import auth_epoch as auth_epoch_service
from muzilla.services import jobs as jobs_service
from muzilla.services import providers as providers_service
from muzilla.services import reset as reset_service
from muzilla.services import settings as settings_service
from muzilla.services.db import session_scope
from muzilla.services.secrets import SecretStore, SecretStoreError

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsSummaryOut)
async def get_settings(
    session: Annotated[Session, Depends(get_session)],
    base_config: Annotated[Config, Depends(get_config)],
    effective_config: Annotated[Config, Depends(get_effective_provider_config)],
) -> settings_service.SettingsSummary:
    return settings_service.get_settings(
        session, provider_config=effective_config, base_config=base_config
    )


@router.put("/settings/providers/{provider}", response_model=ProviderSettingOut)
async def update_provider_setting(
    provider: str,
    request: Request,
    body: UpdateProviderSettingRequest,
    session: Annotated[Session, Depends(get_session)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
    provider_runtime: Annotated[
        providers_service.ProviderSetRuntime, Depends(get_provider_runtime)
    ],
    _sensitive: Annotated[None, Depends(require_sensitive_mutation)],
) -> settings_service.ProviderSetting:
    try:
        settings_service.update_provider_setting(
            session,
            secret_store=secret_store,
            provider=provider,
            enabled=body.enabled,
            token=body.token,
        )
        resolver: providers_service.EffectiveConfigResolver = (
            request.app.state.provider_config_resolver
        )
        effective_config = resolver.resolve(session)
        replacement = providers_service.build_provider_set(effective_config)
        await provider_runtime.swap(replacement, effective_config)
        _schedule_provider_checks(request, provider_runtime)
        return settings_service.provider_setting_from_effective_config(provider, effective_config)
    except settings_service.SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SecretStoreError as exc:
        raise HTTPException(
            status_code=503, detail="provider credentials could not be loaded"
        ) from exc


def _schedule_provider_checks(
    request: Request, provider_runtime: providers_service.ProviderSetRuntime
) -> None:
    task = asyncio.create_task(providers_service.check_all_provider_connections(provider_runtime))
    request.app.state.provider_health_tasks.add(task)
    task.add_done_callback(request.app.state.provider_health_tasks.discard)


async def _refresh_factory_runtime(request: Request, config: Config) -> None:
    """Publish post-factory defaults; retrying this never repeats deletion."""
    runtime: providers_service.ProviderSetRuntime = request.app.state.provider_runtime
    # Secret deletion has committed. Revoke before every fallible refresh step;
    # bootstrap config is safe here because factory reset does not own env/file secrets.
    await runtime.revoke(config)
    resolver: providers_service.EffectiveConfigResolver = request.app.state.provider_config_resolver
    with session_scope(config) as session:
        request.app.state.auth_epoch = auth_epoch_service.read_auth_epoch(session)
        effective_config = resolver.resolve(session)
    replacement = providers_service.build_provider_set(effective_config)
    await runtime.swap(replacement, effective_config)
    _schedule_provider_checks(request, runtime)


async def _wait_for_cross_process_quiesce(config: Config) -> None:
    """Wait for worker processes outside this API controller to acknowledge cancel."""
    while True:
        with session_scope(config) as session:
            if reset_service.workers_are_quiescent(session):
                return
        await asyncio.sleep(config.jobs.cancel_poll_seconds)


@router.put("/settings/templates", response_model=TemplateSettingsOut)
async def update_templates(
    body: UpdateTemplatesRequest,
    session: Annotated[Session, Depends(get_session)],
    _sensitive: Annotated[None, Depends(require_sensitive_mutation)],
) -> settings_service.TemplateSettings:
    return settings_service.update_templates(
        session, album=body.album, singleton=body.singleton, default=body.default
    )


@router.put("/settings/enrichment", response_model=EnrichmentSettingsOut)
async def update_enrichment(
    body: UpdateEnrichmentRequest,
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[Config, Depends(get_config)],
    _sensitive: Annotated[None, Depends(require_sensitive_mutation)],
) -> settings_service.EnrichmentSettings:
    try:
        return settings_service.update_enrichment_settings(
            session,
            config.enrichment,
            metadata_auto=body.metadata_auto,
            art_auto=body.art_auto,
            lyrics_auto=body.lyrics_auto,
            replaygain_auto=body.replaygain_auto,
        )
    except settings_service.SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/settings/paths", response_model=PathsPolicyOut)
async def update_paths_policy(
    body: UpdatePathsPolicyRequest,
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[Config, Depends(get_config)],
    _sensitive: Annotated[None, Depends(require_sensitive_mutation)],
) -> settings_service.PathsPolicySettings:
    try:
        return settings_service.update_paths_policy(
            session,
            config.paths,
            create_directories=body.create_directories,
        )
    except settings_service.SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/settings/strip-fields", response_model=list[str])
async def update_strip_fields(
    body: UpdateStripFieldsRequest,
    session: Annotated[Session, Depends(get_session)],
    _sensitive: Annotated[None, Depends(require_sensitive_mutation)],
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


async def _execute_reset(
    request: Request,
    *,
    scope: reset_service.ResetScope,
    idempotency_key: str,
) -> reset_service.ResetResult:
    config: Config = request.app.state.config
    gate: MutationGate = request.app.state.mutation_gate
    controller: jobs_service.WorkerPoolController = request.app.state.worker_controller
    try:
        async with gate.reset():
            with session_scope(config) as session:
                operation = reset_service.prepare_reset(
                    session,
                    scope=scope,
                    idempotency_key=idempotency_key,
                )
                if operation.state == "succeeded":
                    return reset_service.execute_prepared_reset(
                        session,
                        config=config,
                        secret_store=request.app.state.provider_secret_store,
                        operation_id=operation.id,
                    )
                reset_service.request_worker_quiesce(session)

            await controller.quiesce()
            await _wait_for_cross_process_quiesce(config)
            try:
                with session_scope(config) as session:
                    result = reset_service.execute_prepared_reset(
                        session,
                        config=config,
                        secret_store=request.app.state.provider_secret_store,
                        operation_id=operation.id,
                        defer_completion=scope == reset_service.ResetScope.FACTORY,
                    )
            except reset_service.UnsafeResetTarget:
                request.app.state.worker_task = await controller.start()
                raise

            if scope == reset_service.ResetScope.FACTORY:
                try:
                    await _refresh_factory_runtime(request, config)
                except Exception as exc:
                    raise reset_service.ResetIncomplete(
                        "factory runtime refresh incomplete; retry is required"
                    ) from exc
                with session_scope(config) as session:
                    result = reset_service.complete_reset(session, operation_id=operation.id)
            request.app.state.worker_task = await controller.start()
            return result
    except MutationGateBusy as exc:
        raise HTTPException(status_code=409, detail="another reset is already in progress") from exc
    except reset_service.ResetIdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except reset_service.ResetInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except reset_service.UnsafeResetTarget as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except reset_service.ResetIncomplete as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except reset_service.ResetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/settings/reset/catalog", response_model=ResetResultOut)
async def reset_catalog_and_activity(
    request: Request,
    body: CatalogResetRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    _sensitive: Annotated[None, Depends(require_sensitive_mutation)],
) -> reset_service.ResetResult:
    return await _execute_reset(
        request,
        scope=reset_service.ResetScope.CATALOG_AND_ACTIVITY,
        idempotency_key=idempotency_key,
    )


@router.post("/settings/reset/factory", response_model=ResetResultOut)
async def factory_reset(
    request: Request,
    response: Response,
    body: FactoryResetRequest,
    config: Annotated[Config, Depends(get_config)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    _sensitive: Annotated[None, Depends(require_sensitive_mutation)],
) -> reset_service.ResetResult:
    if not config.auth.enabled:
        raise HTTPException(status_code=409, detail="factory reset requires authentication")
    async with auth_service.verify_semaphore:
        valid_password = auth_service.verify_password(
            request.app.state.auth_password_hash,
            body.password.get_secret_value(),
        )
    if not valid_password:
        raise HTTPException(status_code=403, detail="current password is incorrect")
    result = await _execute_reset(
        request,
        scope=reset_service.ResetScope.FACTORY,
        idempotency_key=idempotency_key,
    )
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        samesite="lax",
        secure=config.auth.cookie_secure,
    )
    return result
