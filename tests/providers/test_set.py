from __future__ import annotations

from pathlib import Path

from muzilla.config.schema import Config, ProviderConfig, ProvidersConfig, StorageConfig
from muzilla.providers.set import build_provider_set


def test_base_url_override_redirects_a_provider_client(tmp_path: Path) -> None:
    """docs/product-spec.md: E2E tests need every provider client pointed
    at a local mock server instead of the real API -- proves the
    override actually reaches the constructed httpx.AsyncClient."""
    config = Config(
        storage=StorageConfig(cache_dir=tmp_path / "cache"),
        providers=ProvidersConfig(
            musicbrainz=ProviderConfig(enabled=True, base_url_override="http://127.0.0.1:9999/mb"),
            discogs=ProviderConfig(enabled=False),
            deezer=ProviderConfig(enabled=False),
            acoustid=ProviderConfig(enabled=False),
            coverartarchive=ProviderConfig(enabled=False),
            lrclib=ProviderConfig(enabled=False),
        ),
    )
    provider_set = build_provider_set(config)

    client = provider_set.metadata["musicbrainz"]._client
    assert str(client.base_url) == "http://127.0.0.1:9999/mb/"


def test_no_override_uses_the_real_default_url(tmp_path: Path) -> None:
    config = Config(
        storage=StorageConfig(cache_dir=tmp_path / "cache"),
        providers=ProvidersConfig(
            musicbrainz=ProviderConfig(enabled=True),
            discogs=ProviderConfig(enabled=False),
            deezer=ProviderConfig(enabled=False),
            acoustid=ProviderConfig(enabled=False),
            coverartarchive=ProviderConfig(enabled=False),
            lrclib=ProviderConfig(enabled=False),
        ),
    )
    provider_set = build_provider_set(config)

    client = provider_set.metadata["musicbrainz"]._client
    assert str(client.base_url) == "https://musicbrainz.org/ws/2/"
