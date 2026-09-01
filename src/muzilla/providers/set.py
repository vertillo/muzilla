"""Provider wiring: turns a `Config` into live, rate-limited provider
clients.

Lives in `muzilla.providers` (not `services`) so both `services/` and
`jobs/` can build/use a `ProviderSet` without a layering violation —
`services` sits above `jobs` in the layered contract, and matching
orchestration needs to run from both a request handler (services) and
a background job handler (jobs) using the exact same code path.
`muzilla.services.providers` re-exports this module's public names so
existing `api`/`cli` imports are unaffected.

Providers are built once per process (cached httpx clients, one per
provider, each with its own on-disk HTTP cache directory) rather than
per-request: constructing a fresh hishel-backed client per call would
throw away the whole point of the cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from muzilla.config.schema import Config
from muzilla.providers.acoustid import AcoustIDProvider
from muzilla.providers.base import ProviderHealth
from muzilla.providers.cache import HttpClientConfig, build_http_client
from muzilla.providers.coverartarchive import CoverArtArchiveProvider
from muzilla.providers.deezer import DeezerProvider
from muzilla.providers.discogs import DiscogsProvider
from muzilla.providers.lrclib import LrcLibProvider
from muzilla.providers.musicbrainz import MusicBrainzProvider

_USER_AGENT = "muzilla/0.1 (https://github.com/muzilla/muzilla)"

_BASE_URLS = {
    "musicbrainz": "https://musicbrainz.org/ws/2",
    "deezer": "https://api.deezer.com",
    "discogs": "https://api.discogs.com",
    "coverartarchive": "https://coverartarchive.org",
    "lrclib": "https://lrclib.net/api",
    "acoustid": "https://api.acoustid.org/v2",
}


@dataclass(frozen=True, slots=True)
class ProviderSet:
    """Every provider client this process knows how to build, gated by
    `ProviderConfig.enabled` — a disabled provider is simply absent
    from the relevant dict, never a client that raises when called."""

    metadata: dict[str, MusicBrainzProvider | DeezerProvider | DiscogsProvider]
    art: dict[str, Any]  # ArtProvider; Any to avoid invariant dict mismatch
    lyrics: dict[str, LrcLibProvider]
    fingerprint: dict[str, AcoustIDProvider]
    clients: tuple[httpx.AsyncClient, ...]
    """Every httpx.AsyncClient created, so callers can close them on
    shutdown (`for c in provider_set.clients: await c.aclose()`)."""


def _client_for(
    config: Config, provider_name: str, extra_headers: dict[str, str] | None = None
) -> httpx.AsyncClient:
    provider_config = getattr(config.providers, provider_name)
    base_url = provider_config.base_url_override or _BASE_URLS[provider_name]
    return build_http_client(
        HttpClientConfig(
            base_url=base_url,
            user_agent=_USER_AGENT,
            cache_dir=config.storage.cache_dir / "http" / provider_name,
            headers=extra_headers,
            provider_name=provider_name,
        )
    )


def build_provider_set(config: Config) -> ProviderSet:
    """Builds every enabled provider's client, wired to its own cache
    directory and (where relevant) its resolved auth token.

    Missing tokens degrade gracefully: a provider
    that requires auth but has none configured is simply left out of
    the enabled set rather than constructed in a broken state — see
    `provider_health` below for surfacing *why* to the UI.
    """
    clients: list[httpx.AsyncClient] = []
    metadata: dict[str, MusicBrainzProvider | DeezerProvider | DiscogsProvider] = {}
    art: dict[str, CoverArtArchiveProvider] = {}
    lyrics: dict[str, LrcLibProvider] = {}
    fingerprint: dict[str, AcoustIDProvider] = {}

    providers_cfg = config.providers

    if providers_cfg.musicbrainz.enabled:
        client = _client_for(config, "musicbrainz")
        clients.append(client)
        metadata["musicbrainz"] = MusicBrainzProvider(client)

    if providers_cfg.deezer.enabled:
        client = _client_for(config, "deezer")
        clients.append(client)
        deezer_provider = DeezerProvider(client)
        metadata["deezer"] = deezer_provider
        # Deezer also provides art via its cover art (cover_xl etc.) — reliably associated via the same album id.
        art["deezer"] = deezer_provider  # type: ignore[assignment]

    if providers_cfg.discogs.enabled:
        token = providers_cfg.discogs.resolved_token()
        if token is not None:
            client = _client_for(config, "discogs")
            clients.append(client)
            metadata["discogs"] = DiscogsProvider(client, token=token)

    if providers_cfg.coverartarchive.enabled:
        client = _client_for(config, "coverartarchive")
        clients.append(client)
        # Only set if not already set by deezer (prefer coverartarchive as primary, but both are available).
        if "coverartarchive" not in art:
            art["coverartarchive"] = CoverArtArchiveProvider(client)
        else:
            # Deezer already added, still add coverartarchive as primary (overwrites if needed, but keeps both).
            art["coverartarchive"] = CoverArtArchiveProvider(client)

    if providers_cfg.lrclib.enabled:
        client = _client_for(config, "lrclib")
        clients.append(client)
        lyrics["lrclib"] = LrcLibProvider(client)

    if providers_cfg.acoustid.enabled:
        token = providers_cfg.acoustid.resolved_token()
        if token is not None:
            client = _client_for(config, "acoustid")
            clients.append(client)
            fingerprint["acoustid"] = AcoustIDProvider(client, api_key=token)

    return ProviderSet(
        metadata=metadata, art=art, lyrics=lyrics, fingerprint=fingerprint, clients=tuple(clients)
    )


async def provider_health(provider_set: ProviderSet) -> list[ProviderHealth]:
    """One health check per built provider. Providers omitted entirely
    (disabled, or auth-required with no token) simply don't appear
    here — callers wanting a full enabled/disabled picture combine
    this with `Config.providers` directly."""
    all_providers = [
        *provider_set.metadata.values(),
        *provider_set.art.values(),
        *provider_set.lyrics.values(),
        *provider_set.fingerprint.values(),
    ]
    return [await p.health() for p in all_providers]
