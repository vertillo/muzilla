"""GET /api/providers/status — passive provider health for the
Dashboard/Settings screens (Phase 7 suggestion #7)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from muzilla.api.deps import get_config, get_provider_set
from muzilla.api.schemas.providers import ProviderStatusListOut
from muzilla.config.schema import Config
from muzilla.services import providers as providers_service
from muzilla.services.providers import ProviderSet

router = APIRouter(tags=["providers"])


@router.get("/providers/status", response_model=ProviderStatusListOut)
async def get_providers_status(
    config: Annotated[Config, Depends(get_config)],
    provider_set: Annotated[ProviderSet, Depends(get_provider_set)],
) -> ProviderStatusListOut:
    items = providers_service.get_provider_status_summary(config, provider_set)
    return ProviderStatusListOut(items=items)  # type: ignore[arg-type]
