"""Security response headers (docs/PLAN.md §12c, step 2.3).

No HSTS here — TLS is terminated by Cloudflare Tunnel or Tailscale
(see README), never by uvicorn directly. An HSTS header from behind the
tunnel would poison the browser against the plain-HTTP LAN path the
same deployment also relies on.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

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
