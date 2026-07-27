"""In-process counters for `/api/metrics` (docs/PLAN.md §11h).

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
    with _lock:
        _provider_requests[(provider_host, outcome)] += 1


def provider_request_counts() -> dict[tuple[str, str], int]:
    with _lock:
        return dict(_provider_requests)
