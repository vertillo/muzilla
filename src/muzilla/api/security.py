"""Origin and session-bound CSRF protection for high-impact mutations."""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request

from muzilla.api.deps import SESSION_COOKIE_NAME


def csrf_token(secret: bytes, *, session_cookie: str | None) -> str:
    binding = session_cookie or "anonymous"
    return hmac.new(secret, binding.encode(), hashlib.sha256).hexdigest()


def _same_origin(request: Request, origin: str) -> bool:
    parsed = urlsplit(origin)
    expected_host = request.headers.get("host", "")
    return (
        parsed.scheme in {"http", "https"}
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and parsed.query == ""
        and parsed.fragment == ""
        and parsed.netloc == expected_host
        and parsed.scheme == request.url.scheme
    )


def require_sensitive_mutation(
    request: Request,
    origin: str | None = Header(default=None, alias="Origin"),
    supplied_csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    if origin is None or not _same_origin(request, origin):
        raise HTTPException(status_code=403, detail="same-origin request required")
    expected = csrf_token(
        request.app.state.csrf_secret,
        session_cookie=request.cookies.get(SESSION_COOKIE_NAME),
    )
    if supplied_csrf is None or not hmac.compare_digest(supplied_csrf, expected):
        raise HTTPException(status_code=403, detail="invalid CSRF token")


__all__ = ["csrf_token", "require_sensitive_mutation"]
