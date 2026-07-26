"""Two cache layers over provider calls, deliberately kept separate
(docs/PLAN.md §2):

- **HTTP cache** — an `hishel`-wrapped `httpx.AsyncClient` respecting
  `ETag`/`Cache-Control`, built once per provider and reused across
  requests so restarts/retries don't re-fetch unnecessarily.
- **Semantic cache** (`provider_cache` table) — stores *normalized*
  `ReleaseCandidate` payloads keyed `(provider, operation, query_hash)`.
  Raw HTTP becomes useless once normalization code changes; the
  semantic cache lets matching re-run offline against already-fetched
  data, which is what makes weight-tuning and tests possible without
  live network calls.

DB access here is sync (SQLAlchemy is sync everywhere per the
"async only at the edges" rule) — callers holding an async provider
call wrap the sync cache read/write in `asyncio.to_thread` if it must
not block the event loop; for the sqlite-file scale this project
targets, a direct call is fine and simpler.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import blake2b
from pathlib import Path

import hishel
import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from muzilla.db.models import ProviderCache

# TTLs per docs/PLAN.md §2.
TTL_RELEASES = timedelta(days=30)
TTL_SEARCHES = timedelta(days=7)
TTL_LYRICS = timedelta(days=90)
TTL_FINGERPRINTS = timedelta(days=180)

TTL_BY_OPERATION: dict[str, timedelta] = {
    "get_release": TTL_RELEASES,
    "search_releases": TTL_SEARCHES,
    "get_lyrics": TTL_LYRICS,
    "get_art": TTL_SEARCHES,
    "fingerprint_lookup": TTL_FINGERPRINTS,
}


def query_hash(*parts: object) -> str:
    """Stable hash of a normalized query/ref tuple for the cache key."""
    canonical = repr(parts).encode()
    return blake2b(canonical).hexdigest()


def cache_get(session: Session, provider: str, operation: str, key: str) -> object | None:
    """Returns the cached payload, or None on miss/expiry.

    An expired row is deleted on read rather than left for a separate
    sweep — cheap, and keeps `provider_cache` from growing unboundedly
    between explicit prune jobs.
    """
    row = session.execute(
        select(ProviderCache).where(
            ProviderCache.provider == provider,
            ProviderCache.operation == operation,
            ProviderCache.query_hash == key,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    expires_at = row.expires_at if row.expires_at.tzinfo is not None else row.expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        session.execute(delete(ProviderCache).where(ProviderCache.id == row.id))
        session.commit()
        return None
    return row.payload


def cache_put(
    session: Session, provider: str, operation: str, key: str, payload: object
) -> None:
    ttl = TTL_BY_OPERATION.get(operation, TTL_SEARCHES)
    expires_at = datetime.now(UTC) + ttl
    existing = session.execute(
        select(ProviderCache).where(
            ProviderCache.provider == provider,
            ProviderCache.operation == operation,
            ProviderCache.query_hash == key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.payload = payload  # type: ignore[assignment]
        existing.expires_at = expires_at
    else:
        session.add(
            ProviderCache(
                provider=provider,
                operation=operation,
                query_hash=key,
                payload=payload,
                expires_at=expires_at,
            )
        )
    session.commit()


@dataclass(frozen=True, slots=True)
class HttpClientConfig:
    base_url: str
    user_agent: str
    cache_dir: Path
    headers: dict[str, str] | None = None
    timeout: float = 15.0


def build_http_client(config: HttpClientConfig) -> httpx.AsyncClient:
    """One hishel-wrapped async client per provider, reused across calls.

    hishel handles ETag/Cache-Control transparently; callers just await
    `client.get(...)` as normal and get free revalidation.
    """
    storage = hishel.AsyncFileStorage(base_path=config.cache_dir)
    controller = hishel.Controller(
        cacheable_methods=["GET"],
        cacheable_status_codes=[200, 203, 300, 301, 308],
        allow_stale=True,
    )
    headers = {"User-Agent": config.user_agent, **(config.headers or {})}
    transport = hishel.AsyncCacheTransport(
        transport=httpx.AsyncHTTPTransport(),
        storage=storage,
        controller=controller,
    )
    return httpx.AsyncClient(
        base_url=config.base_url,
        headers=headers,
        timeout=config.timeout,
        transport=transport,
    )
