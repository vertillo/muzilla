"""FastAPI dependencies: config, per-request DB sessions, and auth.

Handlers depend on `get_session`, never construct one directly, so the
session's lifetime always matches one request.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.services import auth as auth_service
from muzilla.services.db import session_scope
from muzilla.services.providers import ProviderSet

SESSION_COOKIE_NAME = "muzilla_session"


def get_config(request: Request) -> Config:
    return request.app.state.config  # type: ignore[no-any-return]


def get_session(request: Request) -> Iterator[Session]:
    config = get_config(request)
    with session_scope(config) as session:
        yield session


def get_provider_set(request: Request) -> ProviderSet:
    """Built once at app startup (see api/app.py's lifespan) — never
    per-request, since a fresh provider set means fresh httpx clients
    with cold caches."""
    return request.app.state.provider_set  # type: ignore[no-any-return]


def require_auth(request: Request) -> None:
    """Raises 401 unless auth is disabled or a valid session cookie is
    present. Registered per-router rather than as global middleware so
    /api/health and /api/auth/login stay reachable when logged out."""
    config = get_config(request)
    if not config.auth.enabled:
        return

    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_value is None:
        raise HTTPException(status_code=401, detail="not authenticated")

    token = auth_service.verify_session_cookie(config.auth, cookie_value)
    if token is None:
        raise HTTPException(status_code=401, detail="session invalid or expired")
