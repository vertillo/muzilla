from __future__ import annotations

import asyncio
import subprocess
import time
from unittest.mock import patch

from muzilla.config.schema import Config
from muzilla.services.capabilities import (
    CapabilityStatus,
    RuntimeCapabilities,
    RuntimeCapabilityCache,
    probe_replaygain,
)


def test_probe_replaygain_executes_version_instead_of_trusting_path() -> None:
    completed = subprocess.CompletedProcess(
        args=["/usr/local/bin/rsgain", "--version"],
        returncode=127,
        stdout="",
        stderr="loader failure",
    )
    with (
        patch("muzilla.audio.replaygain.shutil.which", return_value="/usr/local/bin/rsgain"),
        patch("muzilla.audio.replaygain.subprocess.run", return_value=completed) as run,
    ):
        result = probe_replaygain(enabled=True)

    run.assert_called_once_with(
        ["/usr/local/bin/rsgain", "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    assert result.available is False
    assert result.state == "unavailable"
    assert "loader failure" not in result.detail


def test_probe_replaygain_reports_successful_process_as_available() -> None:
    completed = subprocess.CompletedProcess(
        args=["/usr/local/bin/rsgain", "--version"],
        returncode=0,
        stdout="rsgain 3.4",
        stderr="",
    )
    with (
        patch("muzilla.audio.replaygain.shutil.which", return_value="/usr/local/bin/rsgain"),
        patch("muzilla.audio.replaygain.subprocess.run", return_value=completed),
    ):
        result = probe_replaygain(enabled=True)

    assert result.available is True
    assert result.state == "available"


def test_probe_replaygain_does_not_start_disabled_capability() -> None:
    with patch("muzilla.audio.replaygain.shutil.which") as which:
        result = probe_replaygain(enabled=False)

    which.assert_not_called()
    assert result.enabled is False
    assert result.available is False
    assert result.state == "disabled"


async def test_runtime_capability_cache_coalesces_concurrent_probes() -> None:
    runtime = RuntimeCapabilities(
        replaygain=CapabilityStatus(
            name="replaygain",
            state="available",
            enabled=True,
            available=True,
            detail="operational",
        ),
        fingerprint=CapabilityStatus(
            name="fingerprint",
            state="available",
            enabled=True,
            available=True,
            detail="operational",
        ),
    )

    def delayed_probe(config: Config) -> RuntimeCapabilities:
        time.sleep(0.05)
        return runtime

    cache = RuntimeCapabilityCache(ttl_seconds=30)
    with patch(
        "muzilla.services.capabilities.get_runtime_capabilities",
        side_effect=delayed_probe,
    ) as probe:
        results = await asyncio.gather(*(cache.get(Config()) for _ in range(20)))

    assert results == [runtime] * 20
    probe.assert_called_once()
