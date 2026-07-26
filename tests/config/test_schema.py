from __future__ import annotations

import pytest
import yaml

from muzilla.config.schema import Config, PathsConfig


def test_paths_config_defaults() -> None:
    config = Config()
    assert config.paths.overrides == {}
    assert config.paths.replace == []


def test_paths_config_overrides_construction() -> None:
    paths = PathsConfig(overrides={"genre:Classical": "Classical/$composer"})
    assert paths.overrides == {"genre:Classical": "Classical/$composer"}


def test_paths_config_overrides_preserve_yaml_order() -> None:
    raw = """
paths:
  overrides:
    "genre:Classical": "c"
    "genre:Jazz": "j"
    "albumartist:Bach": "b"
"""
    data = yaml.safe_load(raw)
    config = Config(**data)
    assert list(config.paths.overrides.keys()) == [
        "genre:Classical",
        "genre:Jazz",
        "albumartist:Bach",
    ]


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
