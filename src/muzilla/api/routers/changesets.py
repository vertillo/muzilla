"""ChangeSet API: GET/PATCH /api/changesets/{id}, apply, undo, plus the
manual-edit/bulk-edit/find-replace/strip endpoints that create DRAFT
ChangeSets, and PATCH /api/tracks/{id}.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from muzilla.api import idempotency
from muzilla.api.deps import get_session
from muzilla.api.schemas.changesets import (
    ApplyDecisionsRequest,
    ApplyRequest,
    ChangeSetDetailOut,
    ChangeSetPageOut,
    FindReplacePreviewOut,
    FindReplacePreviewRowOut,
    FindReplaceRequest,
    StripRequest,
    TrackPatchRequest,
)
from muzilla.api.schemas.jobs import JobEnqueuedOut
from muzilla.api.security import require_sensitive_mutation
from muzilla.services import changesets as changesets_service
from muzilla.services import edit as edit_service
from muzilla.services import settings as settings_service
from muzilla.services import strip as strip_service

router = APIRouter(tags=["changesets"])


@router.get("/changesets", response_model=ChangeSetPageOut)
async def list_changesets(
    session: Annotated[Session, Depends(get_session)],
    state: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> changesets_service.ChangeSetPage:
    return changesets_service.list_changesets(session, state=state, cursor=cursor, limit=limit)


@router.get("/changesets/{change_set_id}", response_model=ChangeSetDetailOut)
async def get_changeset(
    change_set_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> changesets_service.ChangeSetDetail:
    detail = changesets_service.get_changeset(session, change_set_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="changeset not found")
    return detail


@router.patch("/changesets/{change_set_id}/changes", response_model=ChangeSetDetailOut)
async def patch_changes(
    change_set_id: int,
    body: ApplyDecisionsRequest,
    session: Annotated[Session, Depends(get_session)],
) -> changesets_service.ChangeSetDetail:
    decisions = [
        changesets_service.ChangeDecision(
            change_id=d.change_id, decision=d.decision, new_value=d.new_value
        )
        for d in body.decisions
    ]
    try:
        return changesets_service.apply_decisions(session, change_set_id, decisions)
    except changesets_service.ChangeSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/changesets/{change_set_id}/apply", response_model=JobEnqueuedOut, status_code=202
)
async def apply_changeset(
    change_set_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _sensitive: Annotated[None, Depends(require_sensitive_mutation)],
    body: ApplyRequest | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobEnqueuedOut:
    path = f"/changesets/{change_set_id}/apply"
    cached = idempotency.get_cached(request.app.state, path, idempotency_key)
    if cached is not None:
        return JobEnqueuedOut(**cached)

    try:
        job_id = changesets_service.apply(
            session, change_set_id, backup=body.backup if body is not None else None
        )
    except changesets_service.ChangeSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    out = JobEnqueuedOut(job_id=job_id)
    idempotency.store(request.app.state, path, idempotency_key, out.model_dump())
    return out


@router.post(
    "/changesets/{change_set_id}/undo", response_model=JobEnqueuedOut, status_code=202
)
async def undo_changeset(
    change_set_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _sensitive: Annotated[None, Depends(require_sensitive_mutation)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobEnqueuedOut:
    path = f"/changesets/{change_set_id}/undo"
    cached = idempotency.get_cached(request.app.state, path, idempotency_key)
    if cached is not None:
        return JobEnqueuedOut(**cached)

    try:
        job_id = changesets_service.undo(session, change_set_id)
    except changesets_service.ChangeSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    out = JobEnqueuedOut(job_id=job_id)
    idempotency.store(request.app.state, path, idempotency_key, out.model_dump())
    return out


@router.patch("/tracks/{track_id}", response_model=ChangeSetDetailOut)
async def patch_track(
    track_id: int,
    body: TrackPatchRequest,
    session: Annotated[Session, Depends(get_session)],
) -> changesets_service.ChangeSetDetail:
    """Creates a DRAFT ChangeSet from the given field edits — never writes
    directly."""
    try:
        cs = edit_service.edit_track(session, track_id=track_id, field_values=body.fields)
    except edit_service.EditValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()
    detail = changesets_service.get_changeset(session, cs.id)
    assert detail is not None
    return detail



@router.post("/tracks/find-replace/preview", response_model=FindReplacePreviewOut)
async def preview_find_replace(
    body: FindReplaceRequest,
    session: Annotated[Session, Depends(get_session)],
) -> FindReplacePreviewOut:
    try:
        rows = edit_service.preview_find_replace(
            session,
            track_ids=body.track_ids,
            field=body.field,
            find=body.find,
            replace=body.replace,
            use_regex=body.use_regex,
        )
    except edit_service.EditValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # invalid regex, etc.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FindReplacePreviewOut(
        rows=[
            FindReplacePreviewRowOut(track_id=r.track_id, old_value=r.old_value, new_value=r.new_value)
            for r in rows
        ]
    )


@router.post("/tracks/find-replace", response_model=ChangeSetDetailOut)
async def apply_find_replace(
    body: FindReplaceRequest,
    session: Annotated[Session, Depends(get_session)],
) -> changesets_service.ChangeSetDetail:
    try:
        cs = edit_service.apply_find_replace(
            session,
            track_ids=body.track_ids,
            field=body.field,
            find=body.find,
            replace=body.replace,
            use_regex=body.use_regex,
        )
    except edit_service.EditValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    detail = changesets_service.get_changeset(session, cs.id)
    assert detail is not None
    return detail


@router.post("/tracks/strip", response_model=ChangeSetDetailOut)
async def strip_tracks(
    body: StripRequest,
    session: Annotated[Session, Depends(get_session)],
) -> changesets_service.ChangeSetDetail:
    try:
        # Effective strip fields per the /settings override, if one has
        # ever been saved (services/settings.py falls back to domain/
        # fields.py's built-in default_strip set otherwise) — this is
        # the one real behavioral consumer of that setting, not just
        # storage for its own sake.
        strip_field_names = settings_service.get_strip_fields(session)
        cs = strip_service.propose_strip(
            session, track_ids=body.track_ids, strip_field_names=strip_field_names
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    detail = changesets_service.get_changeset(session, cs.id)
    assert detail is not None
    return detail
