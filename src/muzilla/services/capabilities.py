"""Runtime capability probes used by readiness and feature surfaces.

Finding an executable on ``PATH`` is insufficient for dynamically linked
native tools: the loader can still fail after packaging removed a shared
library.  The ReplayGain probe therefore starts the binary with a bounded,
side-effect-free command.  It never analyzes or mutates music files.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Literal

from muzilla.audio.fingerprint import probe_fingerprint_runtime
from muzilla.audio.replaygain import probe_replaygain_runtime
from muzilla.config.schema import Config

CapabilityState = Literal["available", "unavailable", "disabled"]


@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    name: str
    state: CapabilityState
    enabled: bool
    available: bool
    detail: str


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    replaygain: CapabilityStatus
    fingerprint: CapabilityStatus

    @property
    def ready(self) -> bool:
        # fingerprint is not a hard readiness gate; replaygain is the only blocking capability today
        return not self.replaygain.enabled or self.replaygain.available


class CapabilityUnavailableError(RuntimeError):
    """Raised when a requested runtime feature cannot execute."""


def probe_fingerprint(timeout_seconds: float = 5.0) -> CapabilityStatus:
    available, detail = probe_fingerprint_runtime(timeout_seconds=timeout_seconds)
    if available:
        return CapabilityStatus(name="fingerprint", state="available", enabled=True, available=True, detail=detail)
    return CapabilityStatus(name="fingerprint", state="unavailable", enabled=True, available=False, detail=detail)


def probe_replaygain(*, enabled: bool, timeout_seconds: float = 5.0) -> CapabilityStatus:
    """Return whether the distributed ``rsgain`` process can really start.

    Disabled capabilities are not probed and do not make the application
    unready.  Failure details are deliberately stable and sanitized rather
    than returning loader output or host paths through an unauthenticated
    operational endpoint.
    """
    if not enabled:
        return CapabilityStatus(
            name="replaygain",
            state="disabled",
            enabled=False,
            available=False,
            detail="disabled by configuration",
        )

    available, detail = probe_replaygain_runtime(timeout_seconds=timeout_seconds)
    if available:
        return CapabilityStatus(
            name="replaygain",
            state="available",
            enabled=True,
            available=True,
            detail=detail,
        )

    return CapabilityStatus(
        name="replaygain",
        state="unavailable",
        enabled=True,
        available=False,
        detail=detail,
    )


def get_runtime_capabilities(config: Config) -> RuntimeCapabilities:
    return RuntimeCapabilities(
        replaygain=probe_replaygain(enabled=config.enrichment.replaygain_enabled),
        fingerprint=probe_fingerprint(),
    )


def require_replaygain(config: Config) -> CapabilityStatus:
    capability = get_runtime_capabilities(config).replaygain
    if not capability.available:
        raise CapabilityUnavailableError(f"ReplayGain unavailable: {capability.detail}")
    return capability


class RuntimeCapabilityCache:
    """Short-lived single-flight cache for public HTTP capability probes."""

    def __init__(self, *, ttl_seconds: float = 30.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._enabled: bool | None = None
        self._expires_at = 0.0
        self._value: RuntimeCapabilities | None = None

    async def get(self, config: Config) -> RuntimeCapabilities:
        enabled = config.enrichment.replaygain_enabled
        now = time.monotonic()
        if self._value is not None and self._enabled == enabled and now < self._expires_at:
            return self._value

        async with self._lock:
            now = time.monotonic()
            if self._value is not None and self._enabled == enabled and now < self._expires_at:
                return self._value
            value = await asyncio.to_thread(get_runtime_capabilities, config)
            self._enabled = enabled
            self._value = value
            self._expires_at = time.monotonic() + self._ttl_seconds
            return value
