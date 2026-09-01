"""Re-exports `muzilla.providers.set` for existing api/cli imports, plus
the provider-health summary consumed by GET /api/providers/status.

The actual `ProviderSet`/`build_provider_set`/`provider_health`
implementation lives in `muzilla.providers.set`
so `muzilla.jobs` handlers can build/use a `ProviderSet` without
violating the layering contract — `services` sits above `jobs`, and
matching orchestration must run identically from both a request handler
and a background job handler.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import SecretStr
from sqlalchemy.orm import Session

from muzilla.config.schema import Config, ProviderConfig, ProvidersConfig
from muzilla.db.models import Setting
from muzilla.providers import status as provider_status
from muzilla.providers.base import ProviderHealth
from muzilla.providers.runtime import ProviderSetLease, ProviderSetRuntime
from muzilla.providers.set import ProviderSet, build_provider_set, provider_health
from muzilla.services.secrets import SecretStore

__all__ = [
    "EffectiveConfigResolver",
    "ProviderSet",
    "ProviderSetLease",
    "ProviderSetRuntime",
    "ProviderStatusSummary",
    "build_provider_set",
    "check_all_provider_connections",
    "check_provider_connection",
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
    externally_managed: bool
    """True when bootstrap config (env/file) supplies token/token_file, taking precedence over UI-managed."""
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error_detail: str | None
    rate_limited: bool
    state: str
    last_checked_at: datetime | None


def _is_externally_managed(base: ProviderConfig) -> bool:
    return base.token is not None or base.token_file is not None


def is_provider_externally_managed(base_config: Config, provider: str) -> bool:
    if provider not in _ALL_PROVIDER_NAMES:
        return False
    base = getattr(base_config.providers, provider)
    return _is_externally_managed(base)


class EffectiveConfigResolver:
    """Merge bootstrap provider config with durable non-secret overrides.

    Secret values are resolved only into an ephemeral ProviderConfig.  The
    database carries a reference, never a credential; explicit provider env
    variables remain authoritative over database settings.
    External bootstrap secrets (token/token_file via env/file) take precedence
    over UI-managed values and are never overwritten by DB fallback.
    """

    def __init__(self, base_config: Config, secret_store: SecretStore) -> None:
        self._base_config = base_config
        self._secret_store = secret_store

    def resolve(self, session: Session) -> Config:
        resolved: dict[str, ProviderConfig] = {}
        for provider in _ALL_PROVIDER_NAMES:
            base = getattr(self._base_config.providers, provider)
            row = session.get(Setting, f"providers.{provider}")
            stored = dict(row.value) if row is not None else {}
            changes: dict[str, object] = {}

            if "enabled" in stored and not _provider_env_overrides(provider, "enabled"):
                changes["enabled"] = bool(stored["enabled"])

            # External bootstrap secret takes precedence; never consult DB fallback while active.
            if _is_externally_managed(base):
                resolved[provider] = base.model_copy(update=changes)
                continue

            reference = stored.get("secret_ref")
            if (
                isinstance(reference, str)
                and not _provider_env_overrides(provider, "token")
                and not _provider_env_overrides(provider, "token_file")
            ):
                # get() validates the reference and its owner-only file.  It
                # may return None for a missing credential; that deliberately
                # yields a not-configured provider rather than a stale fallback.
                token = self._secret_store.get(reference)
                changes["token"] = SecretStr(token) if token is not None else None
                changes["token_file"] = None
            resolved[provider] = base.model_copy(update=changes)

        return self._base_config.model_copy(update={"providers": ProvidersConfig(**resolved)})


def _provider_env_overrides(provider: str, field: str) -> bool:
    key = f"MUZILLA_PROVIDERS__{provider.upper()}__{field.upper()}"
    return key in os.environ and os.environ[key] != ""


class _HealthProvider(Protocol):
    async def health(self) -> ProviderHealth: ...


def get_provider_status_summary(
    config: Config, provider_set: ProviderSet, base_config: Config | None = None
) -> list[ProviderStatusSummary]:
    """One row per known provider, combining static config (enabled,
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
        externally_managed = False
        if base_config is not None:
            base_provider = getattr(base_config.providers, name)
            externally_managed = _is_externally_managed(base_provider)
        summaries.append(
            ProviderStatusSummary(
                provider=name,
                enabled=provider_config.enabled,
                requires_auth=requires_auth,
                token_configured=token_configured,
                live=name in live_names,
                externally_managed=externally_managed,
                last_success_at=status.last_success_at,
                last_error_at=status.last_error_at,
                last_error_detail=status.last_error_detail,
                rate_limited=status.rate_limited,
                state=_state_for(
                    enabled=provider_config.enabled,
                    requires_auth=requires_auth,
                    token_configured=token_configured,
                    status=status,
                ),
                last_checked_at=status.last_checked_at,
            )
        )
    return summaries


async def check_provider_connection(
    provider_set: ProviderSet,
    provider: str,
    *,
    generation: int | None = None,
    timeout_seconds: float = 5,
) -> None:
    """Probe one live provider and leave a bounded, snapshot-aware result."""
    instance = _provider_instances(provider_set).get(provider)
    if instance is None:
        return
    provider_status.record_checking(provider, generation=generation)
    context_token = provider_status.set_probe_generation(generation)
    try:
        health = await asyncio.wait_for(instance.health(), timeout=timeout_seconds)
    except TimeoutError:
        provider_status.record_check_result(
            provider, healthy=False, detail="connection check timed out", generation=generation
        )
    except Exception:
        # Never propagate an adapter implementation detail to the UI.
        provider_status.record_check_result(
            provider, healthy=False, detail="connection check failed", generation=generation
        )
    else:
        provider_status.record_check_result(
            provider, healthy=health.healthy, detail=health.detail, generation=generation
        )
    finally:
        provider_status.reset_probe_generation(context_token)


async def check_all_provider_connections(provider_runtime: ProviderSetRuntime) -> None:
    """Probe the current snapshot concurrently with a per-provider bound."""
    lease = provider_runtime.acquire()
    try:
        names = _provider_instances(lease.provider_set)
        await asyncio.gather(
            *(
                _check_with_timeout(lease.provider_set, name, generation=lease.generation)
                for name in names
            )
        )
    finally:
        await lease.release()


async def _check_with_timeout(provider_set: ProviderSet, provider: str, *, generation: int) -> None:
    await check_provider_connection(provider_set, provider, generation=generation)


def _provider_instances(provider_set: ProviderSet) -> dict[str, _HealthProvider]:
    return {
        **provider_set.metadata,
        **provider_set.art,
        **provider_set.lyrics,
        **provider_set.fingerprint,
    }


def _state_for(
    *,
    enabled: bool,
    requires_auth: bool,
    token_configured: bool,
    status: provider_status.ProviderStatus,
) -> str:
    if not enabled:
        return "disabled"
    if requires_auth and not token_configured:
        return "not_configured"
    return status.state
