"""FastAPI dependencies: config, per-request DB sessions, and auth.

Handlers depend on `get_session`, never construct one directly, so the
session's lifetime always matches one request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.services import auth as auth_service
from muzilla.services.capabilities import RuntimeCapabilityCache
from muzilla.services.db import session_scope
from muzilla.services.providers import ProviderSet, ProviderSetLease, ProviderSetRuntime
from muzilla.services.secrets import SecretStore

SESSION_COOKIE_NAME = "muzilla_session"


def get_config(request: Request) -> Config:
    return request.app.state.config  # type: ignore[no-any-return]


def get_session(request: Request) -> Iterator[Session]:
    config = get_config(request)
    with session_scope(config) as session:
        yield session


async def get_provider_set(request: Request) -> AsyncIterator[ProviderSet]:
    """Pin one immutable provider-set snapshot for the whole request."""
    runtime: ProviderSetRuntime = request.app.state.provider_runtime
    lease = runtime.acquire()
    try:
        yield lease.provider_set
    finally:
        await lease.release()


async def get_provider_snapshot(request: Request) -> AsyncIterator[ProviderSetLease]:
    """Pin the config and clients that were published together."""
    runtime: ProviderSetRuntime = request.app.state.provider_runtime
    lease = runtime.acquire()
    try:
        yield lease
    finally:
        await lease.release()


def get_provider_runtime(request: Request) -> ProviderSetRuntime:
    return request.app.state.provider_runtime  # type: ignore[no-any-return]


def get_effective_provider_config(request: Request) -> Config:
    runtime: ProviderSetRuntime = request.app.state.provider_runtime
    return runtime.config()


def get_secret_store(request: Request) -> SecretStore:
    return request.app.state.provider_secret_store  # type: ignore[no-any-return]


def get_runtime_capability_cache(request: Request) -> RuntimeCapabilityCache:
    return request.app.state.runtime_capability_cache  # type: ignore[no-any-return]


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
    # A logout bumps app.state.auth_epoch, so a cookie signed before that
    # point stays cryptographically valid (its HMAC still checks out) but
    # must still be rejected — otherwise a cookie captured before logout
    # stays usable for the full 30-day session TTL.
    if token is None or token.is_revoked(current_epoch=request.app.state.auth_epoch):
        raise HTTPException(status_code=401, detail="session invalid or expired")
