"""In-process counters for `/api/metrics`.

Sits outside the layering contract, alongside `muzilla.logging` —
`providers/cache.py`'s response hook increments these, and
`services/metrics.py` reads them; neither module is otherwise allowed
to import the other's layer, so a shared, layer-free module is where
this has to live.

Only for counters with **no DB-queryable equivalent** (provider request
outcomes — there is no persisted request log to count). Track/changeset/
job counts are cheap `COUNT(*)` queries and belong in services/metrics.py
directly, not duplicated here as in-process state that could drift from
the DB or reset on restart.
"""

from __future__ import annotations

from collections import Counter
from threading import Lock

_lock = Lock()
_provider_requests: Counter[tuple[str, str]] = Counter()
"""Keyed (provider_host, outcome) where outcome is "success" (2xx/3xx)
or "error" (4xx/5xx) — coarser than a raw status code, since the
metrics consumer cares about health, not exact codes."""


def record_provider_request(provider_host: str, status_code: int) -> None:
    outcome = "success" if status_code < 400 else "error"
    record_provider_request_outcome(provider_host, outcome)


def record_provider_request_outcome(provider_host: str, outcome: str) -> None:
    """Like `record_provider_request`, but for callers with no HTTP
    status code to derive an outcome from — e.g. a connection failure,
    timeout, or DNS error, which never produces an `httpx.Response` at
    all. providers/cache.py's response-only event hook could never see these,
    so a total provider outage silently
    stopped incrementing the counter instead of showing errors)."""
    with _lock:
        _provider_requests[(provider_host, outcome)] += 1


def provider_request_counts() -> dict[tuple[str, str], int]:
    with _lock:
        return dict(_provider_requests)
