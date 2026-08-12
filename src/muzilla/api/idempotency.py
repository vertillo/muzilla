"""Idempotency-Key support for mutating endpoints (docs/product-spec.md:
"All mutating endpoints accept Idempotency-Key... must not double-apply").

Deliberately minimal for Phase 2: an in-process cache on `app.state`,
keyed by `(request path, Idempotency-Key)`, storing the JSON-serializable
response already produced. This is a single-container app with
single-writer SQLite discipline (CLAUDE.md) and no multi-process
deployment story yet, so a process-local cache is sufficient — a
restart losing in-flight idempotency keys is an acceptable tradeoff at
this phase (the same tradeoff the jobs table will formalize once Phase
4 adds persistent job state). Revisit with a DB-backed table if/when
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
