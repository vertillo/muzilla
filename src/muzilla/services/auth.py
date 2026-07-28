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

import asyncio
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

# Bounds peak login memory to ~2x argon2's per-hash cost (64 MiB default)
# regardless of request rate — the property that actually protects the
# 2G container, since the rate limiter alone doesn't stop a burst spread
# across many source IPs from all landing in the same instant.
verify_semaphore = asyncio.Semaphore(2)


class AuthNotConfiguredError(Exception):
    """Raised when auth is enabled but no password/session_secret is set."""


@dataclass(frozen=True, slots=True)
class SessionToken:
    issued_at: int
    epoch: int

    def is_expired(self, *, now: int | None = None) -> bool:
        current = now if now is not None else int(time.time())
        return current - self.issued_at > _SESSION_TTL_SECONDS

    def is_revoked(self, *, current_epoch: int) -> bool:
        # The epoch this token was issued under, not a timestamp — bumped
        # by logout (services/auth_epoch.py), so a token from a prior
        # epoch is rejected even though its HMAC signature still checks
        # out. Comparing issued_at (a wall-clock time) against
        # current_epoch (a monotonic counter) would silently always be
        # False/True depending on their magnitudes — this bug shipped
        # once and was caught by test_cookie_captured_before_logout_is_
        # rejected_after actually exercising it end to end, not by
        # reasoning about the types.
        return self.epoch < current_epoch


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(password_hash: str, candidate: str) -> bool:
    """`password_hash` is computed once at startup (api/app.py's lifespan,
    from config.auth.resolved_password()) and passed in here rather than
    hashed fresh on every call — argon2's default cost is 64 MiB per
    hash, so re-hashing per login attempt is both slow and a cheap
    unauthenticated memory-amplification DoS against the 2G container
    (~30 concurrent attempts would OOM it). Comparison is still
    constant-time: argon2's `verify` does that, not a raw `==`."""
    try:
        return _hasher.verify(password_hash, candidate)
    except VerifyMismatchError:
        return False


def _session_secret(config: AuthConfig) -> bytes:
    if config.session_secret is None:
        raise AuthNotConfiguredError("MUZILLA_AUTH__SESSION_SECRET is not set")
    return config.session_secret.get_secret_value().encode()


def create_session_cookie(config: AuthConfig, *, epoch: int) -> str:
    issued_at = int(time.time())
    payload = f"{issued_at}.{epoch}".encode()
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
        issued_at_str, epoch_str = payload.decode().split(".", 1)
        issued_at = int(issued_at_str)
        epoch = int(epoch_str)
    except ValueError:
        return None

    token = SessionToken(issued_at=issued_at, epoch=epoch)
    return None if token.is_expired() else token


def _b64(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return urlsafe_b64decode(padded.encode())
