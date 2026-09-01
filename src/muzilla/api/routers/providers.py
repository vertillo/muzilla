"""GET /api/providers/status — passive provider health for the
Dashboard/Settings screens."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from muzilla.api.deps import get_config, get_provider_snapshot
from muzilla.api.schemas.providers import ProviderStatusListOut, ProviderStatusOut
from muzilla.config.schema import Config
from muzilla.services import providers as providers_service
from muzilla.services.providers import ProviderSetLease

router = APIRouter(tags=["providers"])


@router.get("/providers/status", response_model=ProviderStatusListOut)
async def get_providers_status(
    snapshot: Annotated[ProviderSetLease, Depends(get_provider_snapshot)],
    base_config: Annotated[Config, Depends(get_config)],
) -> ProviderStatusListOut:
    assert snapshot.config is not None
    items = providers_service.get_provider_status_summary(
        snapshot.config, snapshot.provider_set, base_config
    )
    out_items = [ProviderStatusOut.model_validate(item) for item in items]
    return ProviderStatusListOut(items=out_items)


@router.post("/providers/{provider}/test", response_model=ProviderStatusOut)
async def test_provider_connection(
    provider: str,
    snapshot: Annotated[ProviderSetLease, Depends(get_provider_snapshot)],
    base_config: Annotated[Config, Depends(get_config)],
) -> ProviderStatusOut:
    if provider not in {"musicbrainz", "discogs", "deezer", "acoustid", "coverartarchive", "lrclib"}:
        raise HTTPException(status_code=404, detail="unknown provider")
    assert snapshot.config is not None
    await providers_service.check_provider_connection(
        snapshot.provider_set, provider, generation=snapshot.generation
    )
    item = next(
        item
        for item in providers_service.get_provider_status_summary(
            snapshot.config, snapshot.provider_set, base_config
        )
        if item.provider == provider
    )
    return ProviderStatusOut.model_validate(item)
