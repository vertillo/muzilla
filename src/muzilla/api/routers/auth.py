"""POST /api/auth/login, POST /api/auth/logout, GET /api/auth/status.

Deliberately outside `require_auth`: these are how a logged-out client
becomes logged in, so they can't themselves require a session.
"""

from __future__ import annotations

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from muzilla.api.deps import SESSION_COOKIE_NAME, get_config, get_session
from muzilla.api.schemas.auth import AuthStatusOut, LoginRequest
from muzilla.api.security import csrf_token
from muzilla.config.schema import Config
from muzilla.services import auth as auth_service
from muzilla.services import auth_epoch as auth_epoch_service
from muzilla.services.ratelimit import FixedWindowLimiter

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days, mirrors services.auth._SESSION_TTL_SECONDS

# Module-level, not per-request: the whole point is to track attempts
# *across* requests. request.client.host is used as the key for now —
# behind a reverse proxy every request arrives from the same address,
# collapsing this into one shared bucket when forwarded headers are not
# configured by a trusted proxy.
_login_limiter = FixedWindowLimiter(limit=5, window_seconds=60)


@router.post("/login")
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    config: Annotated[Config, Depends(get_config)],
) -> AuthStatusOut:
    if not config.auth.enabled:
        return AuthStatusOut(
            enabled=False,
            authenticated=True,
            csrf_token=csrf_token(request.app.state.csrf_secret, session_cookie=None),
        )

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
        cookie_value = auth_service.create_session_cookie(
            config.auth, epoch=request.app.state.auth_epoch
        )
    except auth_service.AuthNotConfiguredError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie_value,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=config.auth.cookie_secure,
    )
    return AuthStatusOut(
        enabled=True,
        authenticated=True,
        csrf_token=csrf_token(request.app.state.csrf_secret, session_cookie=cookie_value),
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    config: Annotated[Config, Depends(get_config)],
    session: Annotated[Session, Depends(get_session)],
) -> AuthStatusOut:
    # secure/samesite must mirror set_cookie's values exactly — a
    # mismatch (e.g. deleting without secure=True when the cookie was
    # set with it) leaves the cookie in place in some browsers, so
    # logout would silently fail to actually clear the session.
    response.delete_cookie(
        SESSION_COOKIE_NAME, samesite="lax", secure=config.auth.cookie_secure
    )
    # Bumps both the DB value and the in-memory app.state copy the same
    # request handles — deleting the cookie alone leaves any
    # already-captured copy of it valid for the rest of its 30-day TTL.
    request.app.state.auth_epoch = auth_epoch_service.bump_auth_epoch(session)
    return AuthStatusOut(
        enabled=True,
        authenticated=False,
        csrf_token=csrf_token(request.app.state.csrf_secret, session_cookie=None),
    )


@router.get("/status")
async def status(
    request: Request,
    config: Annotated[Config, Depends(get_config)],
) -> AuthStatusOut:
    if not config.auth.enabled:
        return AuthStatusOut(
            enabled=False,
            authenticated=True,
            csrf_token=csrf_token(request.app.state.csrf_secret, session_cookie=None),
        )

    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    token = (
        auth_service.verify_session_cookie(config.auth, cookie_value)
        if cookie_value is not None
        else None
    )
    authenticated = token is not None and not token.is_revoked(
        current_epoch=request.app.state.auth_epoch
    )
    return AuthStatusOut(
        enabled=True,
        authenticated=authenticated,
        csrf_token=csrf_token(
            request.app.state.csrf_secret,
            session_cookie=cookie_value if authenticated else None,
        ),
    )
