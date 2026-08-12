"""Idempotency-Key support for mutating endpoints. Repeated requests with
the same key must not double-apply a mutation.

The cache lives on `app.state`, keyed by `(request path, Idempotency-Key)`,
and stores the JSON-serializable response already produced. It is scoped to
the single application process; a restart discards keys for requests that
were not persisted elsewhere.
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
