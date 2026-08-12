"""Security response headers.

No HSTS here — TLS is terminated by Cloudflare Tunnel or Tailscale
(see README), never by uvicorn directly. An HSTS header from behind the
tunnel would poison the browser against the plain-HTTP LAN path the
same deployment also relies on.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from muzilla.services import reset as reset_service
from muzilla.services.db import session_scope

_CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


async def security_headers_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = _CSP
    return response


class MutationGateBusy(RuntimeError):
    pass


class MutationGate:
    """In-process reader/exclusive gate around mutating HTTP requests."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active_mutations = 0
        self._resetting = False

    @asynccontextmanager
    async def mutation(self) -> AsyncIterator[None]:
        async with self._condition:
            if self._resetting:
                raise MutationGateBusy
            self._active_mutations += 1
        try:
            yield
        finally:
            async with self._condition:
                self._active_mutations -= 1
                self._condition.notify_all()

    @asynccontextmanager
    async def reset(self) -> AsyncIterator[None]:
        async with self._condition:
            if self._resetting:
                raise MutationGateBusy
            self._resetting = True
            while self._active_mutations:
                await self._condition.wait()
        try:
            yield
        finally:
            async with self._condition:
                self._resetting = False
                self._condition.notify_all()


async def mutation_gate_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    gate: MutationGate | None = getattr(request.app.state, "mutation_gate", None)
    is_mutation = request.method in {"POST", "PUT", "PATCH", "DELETE"}
    is_reset = request.url.path.startswith("/api/settings/reset/")
    if gate is None or not is_mutation or is_reset:
        return await call_next(request)
    # Login must remain available after a failed factory runtime refresh: the
    # DB auth epoch is already revoked and re-auth is required to retry reset.
    is_auth_recovery = request.url.path in {"/api/auth/login", "/api/auth/logout"}
    config = getattr(request.app.state, "config", None)
    if config is not None and not is_auth_recovery:
        with session_scope(config) as session:
            maintenance_active = reset_service.maintenance_is_active(session)
        if maintenance_active:
            return JSONResponse(
                {"detail": "Muzilla is resetting; mutations are temporarily blocked"},
                status_code=503,
                headers={"Retry-After": "1"},
            )
    try:
        async with gate.mutation():
            return await call_next(request)
    except MutationGateBusy:
        return JSONResponse(
            {"detail": "Muzilla is resetting; mutations are temporarily blocked"},
            status_code=503,
            headers={"Retry-After": "1"},
        )
