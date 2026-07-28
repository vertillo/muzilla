"""Re-exports `muzilla.providers.set` for existing api/cli imports, plus
the provider-health summary consumed by GET /api/providers/status
(Phase 7 suggestion #7).

The actual `ProviderSet`/`build_provider_set`/`provider_health`
implementation lives in `muzilla.providers.set` (moved there in Phase 4)
so `muzilla.jobs` handlers can build/use a `ProviderSet` without
violating the layering contract — `services` sits above `jobs`, and
matching orchestration must run identically from both a request handler
and a background job handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from muzilla.config.schema import Config
from muzilla.providers import status as provider_status
from muzilla.providers.set import ProviderSet, build_provider_set, provider_health

__all__ = [
    "ProviderSet",
    "ProviderStatusSummary",
    "build_provider_set",
    "get_provider_status_summary",
    "provider_health",
]

# Every provider name build_provider_set knows how to build, in the same
# order ProvidersConfig declares them — used so the summary always lists
# every configured provider, including ones omitted from ProviderSet
# entirely (disabled, or auth-required with no token configured), rather
# than only the ones that happened to make a call this process's lifetime.
_ALL_PROVIDER_NAMES = ("musicbrainz", "discogs", "deezer", "acoustid", "coverartarchive", "lrclib")

# requires_auth per each Protocol implementation in providers/*.py —
# duplicated here rather than instantiated-and-inspected, since a
# disabled/token-missing provider never gets built into ProviderSet in
# the first place, so there's no live instance to read .requires_auth
# off of for exactly the providers this needs to flag.
_REQUIRES_AUTH = {"discogs", "acoustid"}


@dataclass(frozen=True, slots=True)
class ProviderStatusSummary:
    provider: str
    enabled: bool
    """Per config.providers.<name>.enabled — the operator's on/off switch."""
    requires_auth: bool
    token_configured: bool
    """True if a token/token_file resolves for providers that need one;
    always True for providers that don't require auth."""
    live: bool
    """True if this provider was actually built into the running
    ProviderSet (enabled AND, if auth-required, a token resolved)."""
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error_detail: str | None
    rate_limited: bool


def get_provider_status_summary(config: Config, provider_set: ProviderSet) -> list[ProviderStatusSummary]:
    """One row per known provider (docs/PLAN.md §9's "provider health"
    dashboard/settings requirement), combining static config (enabled,
    token presence) with the passively-recorded live status from
    providers/status.py — never makes a network call itself."""
    live_names = {
        *provider_set.metadata.keys(),
        *provider_set.art.keys(),
        *provider_set.lyrics.keys(),
        *provider_set.fingerprint.keys(),
    }
    summaries = []
    for name in _ALL_PROVIDER_NAMES:
        provider_config = getattr(config.providers, name)
        requires_auth = name in _REQUIRES_AUTH
        token_configured = not requires_auth or provider_config.resolved_token() is not None
        status = provider_status.get_status(name)
        summaries.append(
            ProviderStatusSummary(
                provider=name,
                enabled=provider_config.enabled,
                requires_auth=requires_auth,
                token_configured=token_configured,
                live=name in live_names,
                last_success_at=status.last_success_at,
                last_error_at=status.last_error_at,
                last_error_detail=status.last_error_detail,
                rate_limited=status.rate_limited,
            )
        )
    return summaries
