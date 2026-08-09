"""Concurrency-safe ownership of the process' live provider clients.

Provider configuration can change while an API request or a worker job is
using a client.  A lease pins the old :class:`ProviderSet` for that operation;
``swap`` publishes a complete replacement first and closes old clients only
after their final lease has returned.  There is therefore never a half-built
set visible to callers and no request has its client closed underneath it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Lock

from muzilla.config.schema import Config
from muzilla.providers.set import ProviderSet


def _empty_provider_set() -> ProviderSet:
    return ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=())


@dataclass(frozen=True, slots=True)
class ProviderSetLease:
    provider_set: ProviderSet
    config: Config | None
    generation: int
    _runtime: ProviderSetRuntime

    async def release(self) -> None:
        await self._runtime._release(self.provider_set)


class ProviderSetRuntime:
    """Publishes provider-set snapshots and retires replaced clients safely."""

    def __init__(self, provider_set: ProviderSet, config: Config | None = None) -> None:
        self._current = provider_set
        self._config = config
        self._leases: dict[int, int] = {}
        self._retiring: dict[int, ProviderSet] = {}
        self._generation = 0
        self._lock = Lock()

    def acquire(self) -> ProviderSetLease:
        with self._lock:
            provider_set = self._current
            identifier = id(provider_set)
            self._leases[identifier] = self._leases.get(identifier, 0) + 1
            return ProviderSetLease(
                provider_set=provider_set,
                config=self._config,
                generation=self._generation,
                _runtime=self,
            )

    def current(self) -> ProviderSet:
        with self._lock:
            return self._current

    def config(self) -> Config:
        with self._lock:
            assert self._config is not None
            return self._config

    async def swap(self, provider_set: ProviderSet, config: Config | None = None) -> None:
        """Atomically make ``provider_set`` current, then retire the old set."""
        to_close: ProviderSet | None = None
        with self._lock:
            previous = self._current
            self._current = provider_set
            self._generation += 1
            if config is not None:
                self._config = config
            identifier = id(previous)
            self._retiring[identifier] = previous
            if self._leases.get(identifier, 0) == 0:
                to_close = self._retiring.pop(identifier)
        if to_close is not None:
            await _close_clients(to_close)

    async def _release(self, provider_set: ProviderSet) -> None:
        to_close: ProviderSet | None = None
        with self._lock:
            identifier = id(provider_set)
            count = self._leases.get(identifier, 0)
            if count <= 1:
                self._leases.pop(identifier, None)
                to_close = self._retiring.pop(identifier, None)
            else:
                self._leases[identifier] = count - 1
        if to_close is not None:
            await _close_clients(to_close)

    async def revoke(self, config: Config | None = None) -> None:
        """Publish an empty snapshot and immediately invalidate older clients.

        Factory reset is a credential-revocation boundary, unlike an ordinary
        settings swap: a lease must not keep a removed token usable.
        """
        with self._lock:
            previous_sets = [self._current, *self._retiring.values()]
            self._current = _empty_provider_set()
            self._retiring.clear()
            self._leases.clear()
            self._generation += 1
            if config is not None:
                self._config = config
        unique_sets = {id(provider_set): provider_set for provider_set in previous_sets}
        await asyncio.gather(
            *(_close_clients(provider_set) for provider_set in unique_sets.values())
        )

    async def close(self) -> None:
        """Close every set owned by this runtime during orderly shutdown."""
        with self._lock:
            provider_sets = [self._current, *self._retiring.values()]
            self._retiring.clear()
            self._leases.clear()
        await asyncio.gather(*(_close_clients(provider_set) for provider_set in provider_sets))


async def _close_clients(provider_set: ProviderSet) -> None:
    await asyncio.gather(*(client.aclose() for client in provider_set.clients))


__all__ = ["ProviderSetLease", "ProviderSetRuntime"]
