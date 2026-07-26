from __future__ import annotations

import pytest

from muzilla.config.schema import Config


def test_jobs_config_defaults() -> None:
    config = Config()
    assert config.jobs.worker_concurrency == 2
    assert config.jobs.poll_interval_seconds == 1.0
    assert config.jobs.job_timeout_seconds == 3600
    assert config.jobs.lease_seconds == 120
    assert config.jobs.event_coalesce_ms == 250


def test_jobs_config_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_JOBS__WORKER_CONCURRENCY", "4")
    monkeypatch.setenv("MUZILLA_JOBS__LEASE_SECONDS", "30")
    config = Config()
    assert config.jobs.worker_concurrency == 4
    assert config.jobs.lease_seconds == 30
