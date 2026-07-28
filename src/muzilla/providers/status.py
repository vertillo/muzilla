"""In-process, passively-recorded provider status (Phase 7 suggestion
#7: "nothing in the UI shows whether MusicBrainz is rate-limiting or a
token is missing; matches simply come back empty, indistinguishable
from 'no match found'").

Deliberately **not** a background poller/heartbeat job — the brief is
explicit that one isn't warranted here. Instead this records status as
a side effect of `providers/cache.py`'s existing single choke points
for outgoing provider calls (`_log_response` for real HTTP responses,
`_FailureRecordingTransport` for connection-level failures), which
already fire for every provider request regardless of caller (UI
search, background import, matching cascade). Piggybacking on those
means "provider health" reflects real traffic with zero extra network
calls, at the cost of showing "unknown" for a provider nobody has
queried yet this process's lifetime — an acceptable tradeoff given the
brief's "keep this cheap" instruction.

Sits alongside `muzilla.metrics` for the same reason that module gives
for living outside the layered package structure: `providers/cache.py`
writes to it and `services/providers.py` reads from it, and neither
module is otherwise allowed to import the other's layer in the reverse
direction needed for a normal one-directional import.

Process-local, not DB-backed or persisted — resets on restart, same
tradeoff `muzilla.metrics` already makes for provider request counts.
A restart also clears any in-flight rate-limit condition upstream, so
"unknown until the next call" is arguably more honest than a stale
persisted status would be.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock

_lock = Lock()


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    provider: str
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error_detail: str | None
    rate_limited: bool
    """True if the most recent recorded response was HTTP 429."""


@dataclass
class _MutableStatus:
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_detail: str | None = None
    rate_limited: bool = False


_status: dict[str, _MutableStatus] = {}


def record_response(provider: str, status_code: int) -> None:
    """Called from providers/cache.py's `_log_response` event hook for
    every provider HTTP response that completed (2xx through 5xx)."""
    with _lock:
        entry = _status.setdefault(provider, _MutableStatus())
        if status_code == 429:
            entry.rate_limited = True
            entry.last_error_at = datetime.now(UTC)
            entry.last_error_detail = "rate limited (HTTP 429)"
        elif status_code < 400:
            entry.rate_limited = False
            entry.last_success_at = datetime.now(UTC)
        else:
            entry.rate_limited = False
            entry.last_error_at = datetime.now(UTC)
            entry.last_error_detail = f"HTTP {status_code}"


def record_error(provider: str, detail: str) -> None:
    """Called from providers/cache.py's `_FailureRecordingTransport` for
    connection-level failures (timeout, DNS, connection refused, ...)
    that never produced an httpx.Response for record_response to see."""
    with _lock:
        entry = _status.setdefault(provider, _MutableStatus())
        entry.rate_limited = False
        entry.last_error_at = datetime.now(UTC)
        entry.last_error_detail = detail


def _to_status(provider: str, entry: _MutableStatus) -> ProviderStatus:
    return ProviderStatus(
        provider=provider,
        last_success_at=entry.last_success_at,
        last_error_at=entry.last_error_at,
        last_error_detail=entry.last_error_detail,
        rate_limited=entry.rate_limited,
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
