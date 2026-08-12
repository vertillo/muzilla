"""Idempotency-Key support for mutating endpoints. Repeated requests with
the same key must not double-apply a mutation.

Deliberately minimal: an in-process cache on `app.state`,
keyed by `(request path, Idempotency-Key)`, storing the JSON-serializable
response already produced. This is a single-container app with
single-writer SQLite discipline (CLAUDE.md) and no multi-process
deployment story yet, so a process-local cache is sufficient for the
single-container deployment — a restart losing in-flight idempotency keys is
an accepted limitation while requests are not persisted across processes.
Revisit with a DB-backed table if/when
multi-worker deployment becomes real.
"""

from __future__ import annotations

from typing import Any

_IDEMPOTENCY_CACHE_ATTR = "_idempotency_cache"


def get_cached(app_state: Any, path: str, key: str | None) -> Any | None:
    if key is None:
        return None
    cache: dict[tuple[str, str], Any] = getattr(app_state, _IDEMPOTENCY_CACHE_ATTR, None) or {}
    return cache.get((path, key))


def store(app_state: Any, path: str, key: str | None, response: Any) -> None:
    if key is None:
        return
    cache: dict[tuple[str, str], Any] | None = getattr(app_state, _IDEMPOTENCY_CACHE_ATTR, None)
    if cache is None:
        cache = {}
        app_state._idempotency_cache = cache
    cache[(path, key)] = response
