from __future__ import annotations

import asyncio

import httpx
import pytest

from muzilla.config.schema import Config
from muzilla.providers import status as provider_status
from muzilla.providers.base import ProviderHealth
from muzilla.providers.runtime import ProviderSetRuntime
from muzilla.providers.set import ProviderSet
from muzilla.services.providers import check_provider_connection


def _provider_set() -> ProviderSet:
    return ProviderSet(
        metadata={}, art={}, lyrics={}, fingerprint={}, clients=(httpx.AsyncClient(),)
    )


@pytest.mark.asyncio
async def test_swap_publishes_a_complete_replacement_before_retiring_old_clients() -> None:
    original = _provider_set()
    replacement = _provider_set()
    runtime = ProviderSetRuntime(original)
    lease = runtime.acquire()

    await runtime.swap(replacement)

    assert runtime.current() is replacement
    assert original.clients[0].is_closed is False

    await lease.release()

    assert original.clients[0].is_closed is True
    await runtime.close()
    assert replacement.clients[0].is_closed is True


class _ControlledHealthProvider:
    def __init__(self, health: ProviderHealth, started: asyncio.Event, release: asyncio.Event) -> None:
        self._health = health
        self._started = started
        self._release = release

    async def health(self) -> ProviderHealth:
        self._started.set()
        await self._release.wait()
        return self._health


@pytest.mark.asyncio
async def test_probe_from_retired_snapshot_cannot_overwrite_new_provider_state() -> None:
    provider_status._status.clear()
    old_started, old_release = asyncio.Event(), asyncio.Event()
    new_started, new_release = asyncio.Event(), asyncio.Event()
    old = ProviderSet(
        metadata={
            "musicbrainz": _ControlledHealthProvider(
                ProviderHealth("musicbrainz", True), old_started, old_release
            )  # type: ignore[dict-item]
        },
        art={}, lyrics={}, fingerprint={}, clients=(),
    )
    new = ProviderSet(
        metadata={
            "musicbrainz": _ControlledHealthProvider(
                ProviderHealth("musicbrainz", False, "HTTP 401"), new_started, new_release
            )  # type: ignore[dict-item]
        },
        art={}, lyrics={}, fingerprint={}, clients=(),
    )
    runtime = ProviderSetRuntime(old, Config())
    old_snapshot = runtime.acquire()
    old_task = asyncio.create_task(
        check_provider_connection(
            old_snapshot.provider_set, "musicbrainz", generation=old_snapshot.generation
        )
    )
    await old_started.wait()

    await runtime.swap(new, Config())
    new_snapshot = runtime.acquire()
    new_release.set()
    await check_provider_connection(new_snapshot.provider_set, "musicbrainz", generation=new_snapshot.generation)
    old_release.set()
    await old_task

    assert provider_status.get_status("musicbrainz").state == "invalid_credentials"
    await old_snapshot.release()
    await new_snapshot.release()
    await runtime.close()
    provider_status._status.clear()
