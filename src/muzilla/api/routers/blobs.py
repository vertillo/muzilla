"""Blob serving API: GET /api/blobs/{id}?size=thumb — the diff review
screen's art thumbnails."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from muzilla.api.deps import get_config, get_session
from muzilla.config.schema import Config
from muzilla.services import blobs as blobs_service

router = APIRouter(tags=["blobs"])


@router.get("/blobs/{blob_id}")
async def get_blob(
    blob_id: int,
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[Config, Depends(get_config)],
    size: str | None = None,
) -> Response:
    result = blobs_service.get_blob_bytes(session, config, blob_id, thumb=(size == "thumb"))
    if result is None:
        raise HTTPException(status_code=404, detail="blob not found")
    return Response(
        content=result.data,
        media_type=result.mime,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
