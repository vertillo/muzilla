"""`GET /api/metrics` — Prometheus exposition format (docs/product-spec.md).

Registered without `Depends(require_auth)` in api/app.py, same as
/api/health — gated on `config.metrics.enabled` instead (default
`False`, since it exposes library size), checked here rather than at
the router-registration level so the 404-vs-403 choice for a disabled
scrape target is explicit and testable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from muzilla.api.deps import get_config, get_session
from muzilla.config.schema import Config
from muzilla.services.metrics import render_metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics(
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[Config, Depends(get_config)],
) -> Response:
    if not config.metrics.enabled:
        raise HTTPException(status_code=404, detail="metrics endpoint is disabled")
    body = render_metrics(session)
    return Response(content=body, media_type="text/plain; version=0.0.4")
