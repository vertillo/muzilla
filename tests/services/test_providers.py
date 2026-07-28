from __future__ import annotations

from pathlib import Path

import pytest

from muzilla.config.schema import Config, ProviderConfig, ProvidersConfig, StorageConfig
from muzilla.providers import status as provider_status
from muzilla.providers.set import build_provider_set
from muzilla.services.providers import get_provider_status_summary


@pytest.fixture(autouse=True)
def _reset_status():
    provider_status._status.clear()
    yield
    provider_status._status.clear()


def _config(tmp_path: Path, **overrides: ProviderConfig) -> Config:
    defaults = {
        "musicbrainz": ProviderConfig(enabled=True),
        "discogs": ProviderConfig(enabled=False),
        "deezer": ProviderConfig(enabled=True),
        "acoustid": ProviderConfig(enabled=True),
        "coverartarchive": ProviderConfig(enabled=True),
        "lrclib": ProviderConfig(enabled=True),
    }
    defaults.update(overrides)
    return Config(storage=StorageConfig(cache_dir=tmp_path / "cache"), providers=ProvidersConfig(**defaults))


def test_summary_lists_every_known_provider(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider_set = build_provider_set(config)

    summary = get_provider_status_summary(config, provider_set)

    assert {s.provider for s in summary} == {
        "musicbrainz",
        "discogs",
        "deezer",
        "acoustid",
        "coverartarchive",
        "lrclib",
    }


def test_disabled_provider_is_not_live(tmp_path: Path) -> None:
    config = _config(tmp_path, discogs=ProviderConfig(enabled=False))
    provider_set = build_provider_set(config)

    summary = {s.provider: s for s in get_provider_status_summary(config, provider_set)}

    assert summary["discogs"].enabled is False
    assert summary["discogs"].live is False


def test_enabled_provider_with_no_auth_required_is_live(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider_set = build_provider_set(config)

    summary = {s.provider: s for s in get_provider_status_summary(config, provider_set)}

    assert summary["musicbrainz"].enabled is True
    assert summary["musicbrainz"].requires_auth is False
    assert summary["musicbrainz"].token_configured is True
    assert summary["musicbrainz"].live is True


def test_auth_required_provider_enabled_but_no_token_is_not_live(tmp_path: Path) -> None:
    # acoustid requires a token; enabled=True with no token configured
    # means build_provider_set omits it from the built ProviderSet
    # entirely (graceful degradation, docs/PLAN.md §8) — the summary
    # must still list it, but flag token_configured=False and live=False
    # so the UI can distinguish "off on purpose" from "misconfigured".
    config = _config(tmp_path, acoustid=ProviderConfig(enabled=True, token=None))
    provider_set = build_provider_set(config)

    summary = {s.provider: s for s in get_provider_status_summary(config, provider_set)}

    assert summary["acoustid"].enabled is True
    assert summary["acoustid"].requires_auth is True
    assert summary["acoustid"].token_configured is False
    assert summary["acoustid"].live is False


def test_auth_required_provider_with_token_is_live(tmp_path: Path) -> None:
    config = _config(tmp_path, acoustid=ProviderConfig(enabled=True, token="a-real-key"))  # type: ignore[arg-type]
    provider_set = build_provider_set(config)

    summary = {s.provider: s for s in get_provider_status_summary(config, provider_set)}

    assert summary["acoustid"].token_configured is True
    assert summary["acoustid"].live is True


def test_summary_reflects_passively_recorded_status(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider_set = build_provider_set(config)
    provider_status.record_response("musicbrainz", 429)

    summary = {s.provider: s for s in get_provider_status_summary(config, provider_set)}

    assert summary["musicbrainz"].rate_limited is True
    assert summary["musicbrainz"].last_error_detail is not None


def test_provider_never_called_has_null_status_fields(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provider_set = build_provider_set(config)

    summary = {s.provider: s for s in get_provider_status_summary(config, provider_set)}

    assert summary["deezer"].last_success_at is None
    assert summary["deezer"].last_error_at is None
    assert summary["deezer"].rate_limited is False
