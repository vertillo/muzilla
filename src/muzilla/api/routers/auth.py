"""POST /api/auth/login, POST /api/auth/logout, GET /api/auth/status.

Deliberately outside `require_auth`: these are how a logged-out client
becomes logged in, so they can't themselves require a session.
"""

from __future__ import annotations

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from muzilla.api.deps import SESSION_COOKIE_NAME, get_config
from muzilla.api.schemas.auth import AuthStatusOut, LoginRequest
from muzilla.config.schema import Config
from muzilla.services import auth as auth_service
from muzilla.services.ratelimit import FixedWindowLimiter

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days, mirrors services.auth._SESSION_TTL_SECONDS

# Module-level, not per-request: the whole point is to track attempts
# *across* requests. request.client.host is used as the key for now —
# behind a reverse proxy every request arrives from the same address,
# collapsing this into one shared bucket, until step 2.5 trusts a
# forwarded-for header instead.
_login_limiter = FixedWindowLimiter(limit=5, window_seconds=60)


@router.post("/login")
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    config: Annotated[Config, Depends(get_config)],
) -> AuthStatusOut:
    if not config.auth.enabled:
        return AuthStatusOut(enabled=False, authenticated=True)

    client_key = request.client.host if request.client else "unknown"
    retry_after = _login_limiter.check(client_key)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="too many login attempts",
            headers={"Retry-After": str(math.ceil(retry_after))},
        )

    password_hash = request.app.state.auth_password_hash
    async with auth_service.verify_semaphore:
        ok = auth_service.verify_password(password_hash, body.password)

    if not ok:
        raise HTTPException(status_code=401, detail="incorrect password")

    _login_limiter.reset(client_key)

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
