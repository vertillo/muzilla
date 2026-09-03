from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from muzilla.api.deps import get_config, get_runtime_capability_cache
from muzilla.api.schemas.health import CapabilitiesOut, CapabilityOut, ReadinessOut
from muzilla.config.schema import Config
from muzilla.services import capabilities as capabilities_service

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Process liveness only; this intentionally performs no native probe."""
    return {"status": "ok"}


async def _capabilities_out(
    config: Config, cache: capabilities_service.RuntimeCapabilityCache
) -> CapabilitiesOut:
    capabilities = await cache.get(config)
    return CapabilitiesOut(
        replaygain=CapabilityOut.model_validate(capabilities.replaygain, from_attributes=True),
        fingerprint=CapabilityOut.model_validate(capabilities.fingerprint, from_attributes=True),
    )


@router.get("/capabilities", response_model=CapabilitiesOut)
async def capabilities(
    config: Annotated[Config, Depends(get_config)],
    cache: Annotated[
        capabilities_service.RuntimeCapabilityCache, Depends(get_runtime_capability_cache)
    ],
) -> CapabilitiesOut:
    return await _capabilities_out(config, cache)


@router.get(
    "/ready",
    response_model=ReadinessOut,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessOut}},
)
async def readiness(
    response: Response,
    config: Annotated[Config, Depends(get_config)],
    cache: Annotated[
        capabilities_service.RuntimeCapabilityCache, Depends(get_runtime_capability_cache)
    ],
) -> ReadinessOut:
    runtime = await cache.get(config)
    if not runtime.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessOut(
        status="ready" if runtime.ready else "not_ready",
        capabilities=CapabilitiesOut(
            replaygain=CapabilityOut.model_validate(runtime.replaygain, from_attributes=True),
            fingerprint=CapabilityOut.model_validate(runtime.fingerprint, from_attributes=True),
        ),
    )
