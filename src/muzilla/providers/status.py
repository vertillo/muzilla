"""In-process state for bounded provider checks and ordinary traffic.

Static config supplies ``disabled`` and ``not_configured``. Live clients are
``checking`` on startup/reload and a probe then records ``operational``,
``temporary_unavailable`` or ``invalid_credentials``. HTTP cache hooks refresh
the same diagnostics without recording request bodies or credentials.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Literal

_lock = Lock()
_probe_generation: ContextVar[int | None] = ContextVar("provider_probe_generation", default=None)


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    provider: str
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error_detail: str | None
    rate_limited: bool
    """True if the most recent recorded response was HTTP 429."""
    state: Literal["checking", "operational", "temporary_unavailable", "invalid_credentials"]
    last_checked_at: datetime | None


@dataclass
class _MutableStatus:
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_detail: str | None = None
    rate_limited: bool = False
    state: Literal["checking", "operational", "temporary_unavailable", "invalid_credentials"] = "checking"
    last_checked_at: datetime | None = None
    probe_generation: int | None = None


_status: dict[str, _MutableStatus] = {}


def set_probe_generation(generation: int | None) -> Token[int | None]:
    """Associate HTTP hooks in this task with a runtime configuration snapshot."""
    return _probe_generation.set(generation)


def reset_probe_generation(token: Token[int | None]) -> None:
    _probe_generation.reset(token)


def _accept_generation(entry: _MutableStatus, generation: int | None, *, result: bool) -> bool:
    if generation is None:
        return True
    if result:
        if entry.probe_generation != generation:
            return False
    elif entry.probe_generation is not None and generation < entry.probe_generation:
        return False
    entry.probe_generation = generation
    return True


def record_response(provider: str, status_code: int, *, generation: int | None = None) -> None:
    """Called from providers/cache.py's `_log_response` event hook for
    every provider HTTP response that completed (2xx through 5xx)."""
    with _lock:
        entry = _status.setdefault(provider, _MutableStatus())
        if not _accept_generation(entry, generation if generation is not None else _probe_generation.get(), result=False):
            return
        if status_code == 429:
            entry.rate_limited = True
            entry.last_error_at = datetime.now(UTC)
            entry.last_error_detail = "rate limited (HTTP 429)"
            entry.state = "temporary_unavailable"
        elif status_code < 400 or status_code == 404:
            entry.rate_limited = False
            entry.last_success_at = datetime.now(UTC)
            entry.state = "operational"
        else:
            entry.rate_limited = False
            entry.last_error_at = datetime.now(UTC)
            entry.last_error_detail = f"HTTP {status_code}"
            entry.state = "invalid_credentials" if status_code in (401, 403) else "temporary_unavailable"


def record_error(provider: str, detail: str, *, generation: int | None = None) -> None:
    """Called from providers/cache.py's `_FailureRecordingTransport` for
    connection-level failures (timeout, DNS, connection refused, ...)
    that never produced an httpx.Response for record_response to see."""
    with _lock:
        entry = _status.setdefault(provider, _MutableStatus())
        if not _accept_generation(entry, generation if generation is not None else _probe_generation.get(), result=False):
            return
        entry.rate_limited = False
        entry.last_error_at = datetime.now(UTC)
        entry.last_error_detail = detail
        entry.state = "temporary_unavailable"


def record_checking(provider: str, *, generation: int | None = None) -> None:
    with _lock:
        entry = _status.setdefault(provider, _MutableStatus())
        if _accept_generation(entry, generation, result=False):
            entry.state = "checking"


def record_check_result(
    provider: str, *, healthy: bool, detail: str = "", generation: int | None = None
) -> None:
    """Store the sanitized result of an explicit or startup health check."""
    with _lock:
        entry = _status.setdefault(provider, _MutableStatus())
        if not _accept_generation(entry, generation, result=True):
            return
        now = datetime.now(UTC)
        entry.last_checked_at = now
        entry.rate_limited = False
        if healthy:
            entry.state = "operational"
            entry.last_success_at = now
            entry.last_error_detail = None
            return
        entry.last_error_at = now
        entry.last_error_detail = _sanitize_detail(detail)
        entry.state = "invalid_credentials" if _is_invalid_credentials(detail) else "temporary_unavailable"


def _to_status(provider: str, entry: _MutableStatus) -> ProviderStatus:
    return ProviderStatus(
        provider=provider,
        last_success_at=entry.last_success_at,
        last_error_at=entry.last_error_at,
        last_error_detail=entry.last_error_detail,
        rate_limited=entry.rate_limited,
        state=entry.state,
        last_checked_at=entry.last_checked_at,
    )


def get_status(provider: str) -> ProviderStatus:
    with _lock:
        entry = _status.get(provider, _MutableStatus())
        return _to_status(provider, entry)


def all_statuses() -> dict[str, ProviderStatus]:
    # threading.Lock is non-reentrant — must not call get_status() (which
    # also acquires _lock) from inside this lock's own `with` block, or
    # every caller deadlocks permanently on the second acquire.
    with _lock:
        return {name: _to_status(name, entry) for name, entry in _status.items()}


def _is_invalid_credentials(detail: str) -> bool:
    lowered = detail.lower()
    return "401" in lowered or "403" in lowered or "unauthorized" in lowered or "forbidden" in lowered


def _sanitize_detail(detail: str) -> str:
    # Do not let a poorly behaved upstream adapter surface a credential in a
    # Settings diagnostic. HTTP errors are still useful without query strings.
    if not detail:
        return "connection check failed"
    return detail.split("?", 1)[0][:240]
