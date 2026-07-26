"""Single-password auth: argon2 password check + signed session cookies.

No user table, no OAuth — one operator password from
`MUZILLA_AUTH__PASSWORD` (or `_FILE`), matching the "self-hosted,
single household" scope. The session token is a signed, timestamped
payload (HMAC-SHA256 over `session_secret`), not a JWT or a DB-backed
session row — there is exactly one session issued at a time and no
distinct-user tracking to justify more machinery.

`MUZILLA_AUTH__ENABLED=false` is the escape hatch for local dev /
trusted-network setups where a password prompt only gets in the way.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from muzilla.config.schema import AuthConfig

_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days
_hasher = PasswordHasher()


class AuthNotConfiguredError(Exception):
    """Raised when auth is enabled but no password/session_secret is set."""


@dataclass(frozen=True, slots=True)
class SessionToken:
    issued_at: int

    def is_expired(self, *, now: int | None = None) -> bool:
        current = now if now is not None else int(time.time())
        return current - self.issued_at > _SESSION_TTL_SECONDS


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(config: AuthConfig, candidate: str) -> bool:
    expected = config.resolved_password()
    if not expected:
        raise AuthNotConfiguredError("MUZILLA_AUTH__PASSWORD is not set")
    # The configured password is plaintext (an env var / secrets file),
    # not a stored hash — hash-then-verify so comparison is still
    # constant-time rather than a raw `==` on secret material.
    hashed = hash_password(expected)
    try:
        return _hasher.verify(hashed, candidate)
    except VerifyMismatchError:
        return False


def _session_secret(config: AuthConfig) -> bytes:
    if config.session_secret is None:
        raise AuthNotConfiguredError("MUZILLA_AUTH__SESSION_SECRET is not set")
    return config.session_secret.get_secret_value().encode()


def create_session_cookie(config: AuthConfig) -> str:
    issued_at = int(time.time())
    payload = str(issued_at).encode()
    sig = hmac.new(_session_secret(config), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(sig)}"


def verify_session_cookie(config: AuthConfig, cookie_value: str) -> SessionToken | None:
    try:
        payload_b64, sig_b64 = cookie_value.split(".", 1)
        payload = _b64decode(payload_b64)
        sig = _b64decode(sig_b64)
    except (ValueError, TypeError):
        return None

    expected_sig = hmac.new(_session_secret(config), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected_sig):
        return None

    try:
        issued_at = int(payload.decode())
    except ValueError:
        return None

    token = SessionToken(issued_at=issued_at)
    return None if token.is_expired() else token


def _b64(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return urlsafe_b64decode(padded.encode())
