"""HTTP and normalized-result cache primitives for provider calls.

- **HTTP cache** — an `hishel`-wrapped `httpx.AsyncClient` respecting
  `ETag`/`Cache-Control`, built once per provider and reused across
  requests so restarts/retries don't re-fetch unnecessarily.
- **Semantic cache** (`provider_cache` table) — stores *normalized*
  provider payloads keyed `(provider, operation, query_hash)`. The
  normalized form keeps cache entries independent of raw response shape;
  callers explicitly decide when to read or write these rows.

DB access here is sync (SQLAlchemy is sync everywhere per the
"async only at the edges" rule) — callers holding an async provider
call wrap the sync cache read/write in `asyncio.to_thread` if it must
not block the event loop; for the sqlite-file scale this project
targets, a direct call is fine and simpler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import blake2b
from pathlib import Path

import hishel
import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from muzilla.db.models import ProviderCache
from muzilla.metrics import record_provider_request, record_provider_request_outcome
from muzilla.providers import status as provider_status

_logger = logging.getLogger(__name__)

# TTLs for normalized provider results.
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

# hishel's own on-disk file GC — separate
# from the semantic ProviderCache DB rows above, which sweep_provider_
# cache() already prunes at their own per-operation TTL. Set safely
# above the longest of those (TTL_FINGERPRINTS, 180 days) so hishel
# never evicts a file the DB-side cache still considers fresh, which
# would force a needless refetch on the next lookup for that entry.
_HISHEL_FILE_TTL_SECONDS = int(timedelta(days=190).total_seconds())


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
    provider_name: str | None = None
    """Config-key name used for passive provider-health tracking."""


async def _log_response(response: httpx.Response, provider_name: str | None) -> None:
    """httpx response event hook for provider request/response status — a
    single choke point covering every provider's outgoing calls, rather
    than adding a log line/counter increment inside each of the six
    provider modules individually. Status + host only, never the body
    (which may carry a token in an error message, and is provider data
    either way, not something worth logging in bulk).

    Only ever fires for requests that got as far as a real HTTP
    response — a connection failure, timeout, or DNS error never
    produces an httpx.Response at all, so those are covered separately
    by _FailureRecordingTransport below; without
    that, a total provider outage left muzilla_provider_requests_total
    flat instead of showing errors, since nothing here could ever see
    the failure)."""
    _logger.info(
        "provider request",
        extra={
            "provider_host": response.request.url.host,
            "method": response.request.method,
            "status_code": response.status_code,
        },
    )
    record_provider_request(response.request.url.host, response.status_code)
    if provider_name is not None:
        provider_status.record_response(provider_name, response.status_code)


class _FailureRecordingTransport(httpx.AsyncBaseTransport):
    """Wraps another transport to record connection-level failures
    (ConnectError, ReadTimeout, DNS errors, ...) as a provider-request
    error outcome, then re-raises unchanged.

    This has to live at the transport layer, not as an httpx event
    hook: hishel's AsyncCacheTransport sits between the client and the
    real network transport, and httpx only fires its "response" event
    hook for requests that actually got a response — a request that
    never completes (the case this exists to catch) has no Response
    object for a hook to receive. Wrapping the innermost transport
    (before hishel wraps it again) means every provider call goes
    through this exactly once, matching _log_response's one-choke-point
    design for the success/HTTP-error side.
    """

    def __init__(self, wrapped: httpx.AsyncBaseTransport, provider_name: str | None) -> None:
        self._wrapped = wrapped
        self._provider_name = provider_name

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._wrapped.handle_async_request(request)
        except httpx.TransportError as exc:
            record_provider_request_outcome(request.url.host, "error")
            if self._provider_name is not None:
                provider_status.record_error(self._provider_name, str(exc))
            raise

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def build_http_client(config: HttpClientConfig) -> httpx.AsyncClient:
    """One hishel-wrapped async client per provider, reused across calls.

    hishel handles ETag/Cache-Control transparently; callers just await
    `client.get(...)` as normal and get free revalidation.
    """
    storage = hishel.AsyncFileStorage(
        base_path=config.cache_dir,
        ttl=_HISHEL_FILE_TTL_SECONDS,
    )
    controller = hishel.Controller(
        cacheable_methods=["GET"],
        cacheable_status_codes=[200, 203, 300, 301, 308],
        allow_stale=True,
    )
    headers = {"User-Agent": config.user_agent, **(config.headers or {})}

    async def _response_hook(response: httpx.Response) -> None:
        await _log_response(response, config.provider_name)

    transport = hishel.AsyncCacheTransport(
        transport=_FailureRecordingTransport(httpx.AsyncHTTPTransport(), config.provider_name),
        storage=storage,
        controller=controller,
    )
    return httpx.AsyncClient(
        base_url=config.base_url,
        headers=headers,
        timeout=config.timeout,
        transport=transport,
        event_hooks={"response": [_response_hook]},
    )
