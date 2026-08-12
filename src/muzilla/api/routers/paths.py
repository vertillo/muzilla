"""Path template API: live preview + rename staging (docs/product-spec.md, §10)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from muzilla.api.deps import get_config, get_session
from muzilla.api.schemas.changesets import ChangeSetDetailOut
from muzilla.api.schemas.paths import (
    PathPreviewOut,
    PathPreviewRequest,
    PathPreviewRowOut,
    PathRenameRequest,
)
from muzilla.config.schema import Config
from muzilla.services import changesets as changesets_service
from muzilla.services import paths as paths_service
from muzilla.services import settings as settings_service

router = APIRouter(tags=["paths"])


@router.post("/paths/preview", response_model=PathPreviewOut)
async def preview_paths(
    body: PathPreviewRequest,
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[Config, Depends(get_config)],
) -> PathPreviewOut:
    try:
        rows = paths_service.preview_rename(
            session,
            track_ids=body.track_ids,
            group_id=body.group_id,
            config=settings_service.effective_paths_config(session, config.paths),
            template_override=body.template,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PathPreviewOut(
        rows=[
            PathPreviewRowOut(
                track_id=r.track_id,
                old_path=r.old_path,
                new_path=r.new_path,
                errors=r.errors,
                is_collision=r.is_collision,
            )
            for r in rows
        ]
    )


@router.post("/paths/rename", response_model=ChangeSetDetailOut)
async def rename_paths(
    body: PathRenameRequest,
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[Config, Depends(get_config)],
) -> changesets_service.ChangeSetDetail:
    try:
        cs = paths_service.stage_rename(
            session,
            track_ids=body.track_ids,
            group_id=body.group_id,
            config=settings_service.effective_paths_config(session, config.paths),
            template_override=body.template,
        )
    except (paths_service.PathValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    detail = changesets_service.get_changeset(session, cs.id)
    assert detail is not None
    return detail
