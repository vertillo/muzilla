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

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import blake2b
from pathlib import Path
from typing import Any

import hishel
import httpx
from sqlalchemy import select
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

CACHE_VERSION = 1
"""Version for provider_cache payload format; mismatched version is treated as miss."""

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


def _is_fresh(row: ProviderCache) -> bool:
    if getattr(row, "schema_version", CACHE_VERSION) != CACHE_VERSION:
        return False
    expires_at = (
        row.expires_at if row.expires_at.tzinfo is not None else row.expires_at.replace(tzinfo=UTC)
    )
    return expires_at > datetime.now(UTC)


def cache_get(session: Session, provider: str, operation: str, key: str) -> object | None:
    """Returns the cached payload, or None on miss/expiry/version-mismatch.

    An expired or version-mismatched row is treated as miss and deleted (flushed, not committed)
    — cheap, and keeps `provider_cache` from growing unboundedly
    between explicit prune jobs. Callers must commit/rollback as appropriate.
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
    if not _is_fresh(row):
        session.delete(row)
        session.flush()
        return None
    return row.payload


def cache_get_fresh(session: Session, provider: str, operation: str, key: str) -> object | None:
    """Fresh-only read: returns payload only if not expired and version matches; never deletes."""
    row = session.execute(
        select(ProviderCache).where(
            ProviderCache.provider == provider,
            ProviderCache.operation == operation,
            ProviderCache.query_hash == key,
        )
    ).scalar_one_or_none()
    if row is None or not _is_fresh(row):
        return None
    return row.payload


def cache_get_stale(session: Session, provider: str, operation: str, key: str) -> object | None:
    """Stale-tolerant read: returns payload even if expired, but only if version matches."""
    row = session.execute(
        select(ProviderCache).where(
            ProviderCache.provider == provider,
            ProviderCache.operation == operation,
            ProviderCache.query_hash == key,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if getattr(row, "schema_version", CACHE_VERSION) != CACHE_VERSION:
        return None
    return row.payload


def cache_get_with_provenance(
    session: Session, provider: str, operation: str, key: str
) -> tuple[object | None, dict[str, object]]:
    """Returns (payload, provenance) where provenance includes cached, stale, version, cached_at."""
    row = session.execute(
        select(ProviderCache).where(
            ProviderCache.provider == provider,
            ProviderCache.operation == operation,
            ProviderCache.query_hash == key,
        )
    ).scalar_one_or_none()
    if row is None:
        return None, {"cached": False, "stale": False, "version": CACHE_VERSION}
    is_fresh = _is_fresh(row)
    provenance: dict[str, object] = {
        "cached": True,
        "stale": not is_fresh,
        "version": getattr(row, "schema_version", CACHE_VERSION),
        "cached_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }
    if getattr(row, "schema_version", CACHE_VERSION) != CACHE_VERSION:
        return None, {"cached": False, "stale": False, "version": CACHE_VERSION}
    return row.payload, provenance


def cache_put(session: Session, provider: str, operation: str, key: str, payload: object) -> None:
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
        if hasattr(existing, "schema_version"):
            existing.schema_version = CACHE_VERSION
    else:
        session.add(
            ProviderCache(
                provider=provider,
                operation=operation,
                query_hash=key,
                payload=payload,
                expires_at=expires_at,
                schema_version=CACHE_VERSION,
            )
        )
    session.flush()


# --- Semantic gateway helpers for all reachable provider operations ---
# Each helper implements fresh/stale/offline/refresh semantics and transaction durability
# (cache writes are flushed, not committed, so callers can commit only cache work safely).


def _is_offline(config: object | None) -> bool:
    return bool(config and getattr(config, "providers_offline", False))


async def cached_get_release(
    session: Session | None,
    config: object | None,
    provider: Any,
    ref: Any,
    *,
    refresh: bool = False,
) -> tuple[object | None, dict[str, object]]:
    """Cached get_release with provenance. Returns (candidate_or_none, provenance)."""
    from muzilla.providers.base import ProviderRef as _PR
    from muzilla.providers.base import ReleaseCandidate as _RC

    def _payload_to_candidate(payload: object) -> Any:
        if isinstance(payload, _RC):
            return payload
        if not isinstance(payload, dict):
            return payload
        # Full reconstruction without importing matching layer (providers must not import matching).
        try:
            from muzilla.providers.base import ArtRef as _AR
            from muzilla.providers.base import CandidateTrack as _CT

            ref_data2 = payload.get("ref", {})
            if isinstance(ref_data2, dict):
                ref_obj2 = _PR(provider=str(ref_data2.get("provider", "")), id=str(ref_data2.get("id", "")))
            elif isinstance(ref_data2, _PR):
                ref_obj2 = ref_data2
            else:
                ref_obj2 = ref
            tracks_data = payload.get("tracks", [])
            tracks: tuple[_CT, ...] = ()
            if isinstance(tracks_data, list):
                t_list = []
                for t in tracks_data:
                    if isinstance(t, dict):
                        t_list.append(_CT(position=int(t.get("position", 1)), title=str(t.get("title", "")), artist=t.get("artist"), duration_ms=t.get("duration_ms"), disc_number=t.get("disc_number"), isrc=t.get("isrc"), mb_track_id=t.get("mb_track_id"), mb_recording_id=t.get("mb_recording_id")))
                tracks = tuple(t_list)
            art_refs_data = payload.get("art_refs", [])
            art_refs: tuple[_AR, ...] = ()
            if isinstance(art_refs_data, list):
                a_list = []
                for a in art_refs_data:
                    if isinstance(a, dict) and "url" in a:
                        a_list.append(_AR(url=str(a["url"]), source=str(a.get("source", "")), width=a.get("width"), height=a.get("height"), mime=a.get("mime")))
                art_refs = tuple(a_list)
            return _RC(source=str(payload.get("source", "")), ref=ref_obj2, album=payload.get("album"), album_artist=payload.get("album_artist"), year=payload.get("year"), tracks=tracks, art_refs=art_refs, candidate_type=str(payload.get("candidate_type", "release")), track_count=payload.get("track_count"))
        except Exception:
            try:
                ref_data = payload.get("ref", {})
                if isinstance(ref_data, dict):
                    ref_obj = _PR(
                        provider=str(ref_data.get("provider", "")), id=str(ref_data.get("id", ""))
                    )
                elif isinstance(ref_data, _PR):
                    ref_obj = ref_data
                else:
                    ref_obj = ref
                return _RC(
                    source=str(payload.get("source", getattr(payload, "source", ""))),
                    ref=ref_obj,
                    album=payload.get("album"),
                    album_artist=payload.get("album_artist"),
                    year=payload.get("year"),
                )
            except Exception:
                return payload

    provider_name = getattr(provider, "name", "unknown")
    key = query_hash(provider_name, "get_release", ref.id) if session is not None else None
    is_offline = _is_offline(config)
    if session is not None and key is not None and not refresh and not is_offline:
        fresh = cache_get_fresh(session, provider_name, "get_release", key)
        if fresh is not None:
            # Fresh payload is a dict from asdict; convert back to ReleaseCandidate for caller.
            cand = _payload_to_candidate(fresh)
            return cand, {"cached": True, "stale": False, "offline": False}
    if is_offline and session is not None and key is not None:
        stale = cache_get_stale(session, provider_name, "get_release", key)
        if stale is not None:
            cand2 = _payload_to_candidate(stale)
            return cand2, {"cached": True, "stale": True, "offline": True}
        return None, {"cached": False, "stale": False, "offline": True}
    try:
        result = await provider.get_release(ref)
        if result is not None and session is not None and key is not None:
            # Serialize candidate for cache.
            from dataclasses import asdict as _asdict

            payload = {
                "source": result.source,
                "ref": {"provider": result.ref.provider, "id": result.ref.id},
                "album": result.album,
                "album_artist": result.album_artist,
                "year": result.year,
            }
            try:
                payload = _asdict(result)
            except Exception:
                payload = {
                    "source": result.source,
                    "ref": {"provider": result.ref.provider, "id": result.ref.id},
                }
            cache_put(session, provider_name, "get_release", key, payload)
            session.flush()
        return result, {"cached": False, "stale": False, "offline": False}
    except Exception as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        if session is not None and key is not None:
            stale = cache_get_stale(session, provider_name, "get_release", key)
            if stale is not None:
                cand3 = _payload_to_candidate(stale)
                return cand3, {"cached": True, "stale": True, "offline": False}
        raise


async def cached_get_track(
    session: Session | None,
    config: object | None,
    provider: Any,
    ref: Any,
    *,
    refresh: bool = False,
) -> tuple[Any | None, dict[str, object]]:
    provider_name = getattr(provider, "name", "unknown")
    key = query_hash(provider_name, "get_track", ref.id) if session is not None else None
    is_offline = _is_offline(config)
    if session is not None and key is not None and not refresh and not is_offline:
        fresh = cache_get_fresh(session, provider_name, "get_track", key)
        if fresh is not None:
            # Fresh payload is a dict from asdict; reconstruct minimally without importing matching layer.
            # For get_track, the payload is a ReleaseCandidate dict; we can return it as-is and let the caller handle it,
            # or reconstruct a minimal ReleaseCandidate.
            # To avoid layer violation, we return the dict directly; the caller (manual_search) will handle both cases.
            return fresh, {"cached": True, "stale": False, "offline": False}
    if is_offline and session is not None and key is not None:
        stale = cache_get_stale(session, provider_name, "get_track", key)
        if stale is not None:
            return stale, {"cached": True, "stale": True, "offline": True}
        return None, {"cached": False, "stale": False, "offline": True}
    try:
        result = await provider.get_track_candidate(ref)
        if result is not None and session is not None and key is not None:
            from dataclasses import asdict as _asdict3

            try:
                payload3 = _asdict3(result)
            except Exception:
                payload3 = {
                    "source": result.source,
                    "ref": {"provider": result.ref.provider, "id": result.ref.id},
                }
            cache_put(session, provider_name, "get_track", key, payload3)
            session.flush()
        return result, {"cached": False, "stale": False, "offline": False}
    except Exception as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        if session is not None and key is not None:
            stale2 = cache_get_stale(session, provider_name, "get_track", key)
            if stale2 is not None:
                return stale2, {"cached": True, "stale": True, "offline": False}
        raise


async def cached_get_art(
    session: Session | None,
    config: object | None,
    provider: Any,
    ref: Any,
    *,
    refresh: bool = False,
) -> tuple[Any | None, dict[str, object]]:
    from muzilla.providers.base import ArtRef as _ArtRef

    def _payload_to_artrefs(payload: object) -> Any | None:
        if isinstance(payload, list) and payload and isinstance(payload[0], _ArtRef):
            return payload
        if not isinstance(payload, list):
            return None
        art_refs: list[_ArtRef] = []
        for item in payload:
            if isinstance(item, dict) and "url" in item:
                art_refs.append(
                    _ArtRef(
                        url=str(item["url"]),
                        source=str(item.get("source", "")),
                        width=item.get("width"),
                        height=item.get("height"),
                        mime=item.get("mime"),
                    )
                )
            elif isinstance(item, _ArtRef):
                art_refs.append(item)
        return art_refs

    provider_name = getattr(provider, "name", "coverartarchive")
    key = query_hash(provider_name, "get_art", ref.id) if session is not None else None
    is_offline = _is_offline(config)
    if session is not None and key is not None and not refresh and not is_offline:
        fresh = cache_get_fresh(session, provider_name, "get_art", key)
        if fresh is not None and isinstance(fresh, list):
            arts = _payload_to_artrefs(fresh)
            if arts is not None:
                return arts, {"cached": True, "stale": False, "offline": False}
    if is_offline and session is not None and key is not None:
        stale = cache_get_stale(session, provider_name, "get_art", key)
        if stale is not None and isinstance(stale, list):
            arts2 = _payload_to_artrefs(stale)
            if arts2 is not None:
                return arts2, {"cached": True, "stale": True, "offline": True}
        return None, {"cached": False, "stale": False, "offline": True}
    try:
        result = await provider.get_art(ref)
        if session is not None and key is not None:
            payload = [
                {
                    "url": r.url,
                    "source": r.source,
                    "width": r.width,
                    "height": r.height,
                    "mime": r.mime,
                }
                for r in result
            ]
            cache_put(session, provider_name, "get_art", key, payload)
            session.flush()
        return result, {"cached": False, "stale": False, "offline": False}
    except Exception as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        if session is not None and key is not None:
            stale = cache_get_stale(session, provider_name, "get_art", key)
            if stale is not None and isinstance(stale, list):
                arts3 = _payload_to_artrefs(stale)
                if arts3 is not None:
                    return arts3, {"cached": True, "stale": True, "offline": False}
        raise


async def cached_get_lyrics(
    session: Session | None,
    config: object | None,
    provider: Any,
    artist: str,
    title: str,
    duration_ms: int | None,
    *,
    refresh: bool = False,
) -> tuple[object | None, dict[str, object]]:
    from muzilla.domain.metadata import LyricsResult as _LyricsResult

    def _payload_to_lyrics(payload: object) -> Any | None:
        if isinstance(payload, _LyricsResult):
            return payload
        if payload is None:
            return None
        if isinstance(payload, dict):
            try:
                return _LyricsResult(
                    text=str(payload.get("text", "")),
                    synced=bool(payload.get("synced", False)),
                    source=str(payload.get("source", "lrclib")),
                )
            except Exception:
                return payload
        return payload

    provider_name = getattr(provider, "name", "lrclib")
    key = (
        query_hash(provider_name, "get_lyrics", artist, title, duration_ms)
        if session is not None
        else None
    )
    is_offline = _is_offline(config)
    if session is not None and key is not None and not refresh and not is_offline:
        fresh = cache_get_fresh(session, provider_name, "get_lyrics", key)
        if fresh is not None:
            lyr = _payload_to_lyrics(fresh)
            return lyr, {"cached": True, "stale": False, "offline": False}
    if is_offline and session is not None and key is not None:
        stale = cache_get_stale(session, provider_name, "get_lyrics", key)
        if stale is not None:
            lyr2 = _payload_to_lyrics(stale)
            return lyr2, {"cached": True, "stale": True, "offline": True}
        return None, {"cached": False, "stale": False, "offline": True}
    try:
        result = await provider.get_lyrics(artist, title, duration_ms)
        if session is not None and key is not None:
            from dataclasses import asdict as _asdict2

            payload = _asdict2(result) if result is not None else None
            cache_put(session, provider_name, "get_lyrics", key, payload)
            session.flush()
        return result, {"cached": False, "stale": False, "offline": False}
    except Exception as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        if session is not None and key is not None:
            stale = cache_get_stale(session, provider_name, "get_lyrics", key)
            if stale is not None:
                lyr3 = _payload_to_lyrics(stale)
                return lyr3, {"cached": True, "stale": True, "offline": False}
        raise


async def cached_fingerprint_lookup(
    session: Session | None,
    config: object | None,
    provider: Any,
    fingerprint: str,
    duration_s: float,
    *,
    refresh: bool = False,
) -> tuple[list[object] | None, dict[str, object]]:
    provider_name = getattr(provider, "name", "acoustid")
    key = (
        query_hash(provider_name, "fingerprint_lookup", fingerprint, duration_s)
        if session is not None
        else None
    )
    is_offline = _is_offline(config)
    if session is not None and key is not None and not refresh and not is_offline:
        fresh = cache_get_fresh(session, provider_name, "fingerprint_lookup", key)
        if fresh is not None and isinstance(fresh, list):
            return fresh, {"cached": True, "stale": False, "offline": False}
    if is_offline and session is not None and key is not None:
        stale = cache_get_stale(session, provider_name, "fingerprint_lookup", key)
        if stale is not None and isinstance(stale, list):
            return stale, {"cached": True, "stale": True, "offline": True}
        return None, {"cached": False, "stale": False, "offline": True}
    try:
        result = await provider.lookup(fingerprint, duration_s)
        if session is not None and key is not None:
            payload = [
                {
                    "mb_recording_id": m.mb_recording_id,
                    "mb_release_ids": list(m.mb_release_ids),
                    "score": m.score,
                }
                for m in result
            ]
            cache_put(session, provider_name, "fingerprint_lookup", key, payload)
            session.flush()
        return result, {"cached": False, "stale": False, "offline": False}
    except Exception as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        if session is not None and key is not None:
            stale = cache_get_stale(session, provider_name, "fingerprint_lookup", key)
            if stale is not None and isinstance(stale, list):
                return stale, {"cached": True, "stale": True, "offline": False}
        raise


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
