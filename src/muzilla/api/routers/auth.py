"""POST /api/auth/login, POST /api/auth/logout, GET /api/auth/status.

Deliberately outside `require_auth`: these are how a logged-out client
becomes logged in, so they can't themselves require a session.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from muzilla.api.deps import SESSION_COOKIE_NAME, get_config
from muzilla.api.schemas.auth import AuthStatusOut, LoginRequest
from muzilla.config.schema import Config
from muzilla.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days, mirrors services.auth._SESSION_TTL_SECONDS


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    config: Annotated[Config, Depends(get_config)],
) -> AuthStatusOut:
    if not config.auth.enabled:
        return AuthStatusOut(enabled=False, authenticated=True)

    try:
        ok = auth_service.verify_password(config.auth, body.password)
    except auth_service.AuthNotConfiguredError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not ok:
        raise HTTPException(status_code=401, detail="incorrect password")

    try:
        cookie_value = auth_service.create_session_cookie(config.auth)
    except auth_service.AuthNotConfiguredError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie_value,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return AuthStatusOut(enabled=True, authenticated=True)


@router.post("/logout")
async def logout(response: Response) -> AuthStatusOut:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return AuthStatusOut(enabled=True, authenticated=False)


@router.get("/status")
async def status(
    request: Request,
    config: Annotated[Config, Depends(get_config)],
) -> AuthStatusOut:
    if not config.auth.enabled:
        return AuthStatusOut(enabled=False, authenticated=True)

    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    authenticated = cookie_value is not None and (
        auth_service.verify_session_cookie(config.auth, cookie_value) is not None
    )
    return AuthStatusOut(enabled=True, authenticated=authenticated)
