from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from muzilla.providers.base import ProviderRef, ReleaseQuery
from muzilla.providers.discogs import DiscogsProvider

FIXTURES = Path(__file__).parent.parent / "fixtures" / "providers" / "discogs"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://api.discogs.com")


@pytest.mark.asyncio
async def test_search_releases_maps_fields(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.discogs.com/database/search").mock(
        return_value=httpx.Response(200, json=_load("search_release.json"))
    )
    provider = DiscogsProvider(client, token="test-token")
    results = await provider.search_releases(ReleaseQuery(album="Ágætis byrjun", album_artist="Sigur Rós"), limit=5)

    assert len(results) == 1
    candidate = results[0]
    assert candidate.source == "discogs"
    assert candidate.album == "Ágætis Byrjun"
    assert candidate.album_artist == "Sigur Rós"
    assert candidate.year == 1999
    assert candidate.label == "Fat Cat Records"
    assert candidate.catalog_number == "FAT007CD"
    assert candidate.country == "UK"
    assert candidate.discogs_release_id == "439334"

    request = respx_mock.calls.last.request
    assert request.headers["Authorization"] == "Discogs token=test-token"


@pytest.mark.asyncio
async def test_get_release_parses_tracklist_and_barcode(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://api.discogs.com/releases/439334").mock(
        return_value=httpx.Response(200, json=_load("get_release.json"))
    )
    provider = DiscogsProvider(client, token="test-token")
    candidate = await provider.get_release(ProviderRef(provider="discogs", id="439334"))

    assert candidate is not None
    assert candidate.album == "Ágætis Byrjun"
    assert candidate.album_artist == "Sigur Rós"
    assert candidate.label == "Fat Cat Records"
    assert candidate.catalog_number == "FAT007CD"
    assert candidate.barcode == "5028983207323"
    assert len(candidate.tracks) == 2
    track1, track2 = candidate.tracks
    assert track1.title == "Intro"
    assert track1.duration_ms == 97000
    assert track2.title == "Svefn-G-Englar"
    assert track2.duration_ms == 601000


@pytest.mark.asyncio
async def test_get_release_404_returns_none(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.discogs.com/releases/0").mock(return_value=httpx.Response(404))
    provider = DiscogsProvider(client, token="test-token")
    result = await provider.get_release(ProviderRef(provider="discogs", id="0"))
    assert result is None


@pytest.mark.asyncio
async def test_no_token_raises_clear_error_on_search(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    provider = DiscogsProvider(client, token=None)
    with pytest.raises(RuntimeError, match="no token"):
        await provider.search_releases(ReleaseQuery(album="x"), limit=5)
    assert respx_mock.calls.call_count == 0


@pytest.mark.asyncio
async def test_no_token_raises_clear_error_on_get_release(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    provider = DiscogsProvider(client, token=None)
    with pytest.raises(RuntimeError, match="no token"):
        await provider.get_release(ProviderRef(provider="discogs", id="439334"))


@pytest.mark.asyncio
async def test_health_reports_unhealthy_without_token(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    provider = DiscogsProvider(client, token=None)
    health = await provider.health()
    assert health.healthy is False
    assert "token" in health.detail
    assert respx_mock.calls.call_count == 0


@pytest.mark.asyncio
async def test_health_ok_with_token(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.discogs.com/database/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    provider = DiscogsProvider(client, token="test-token")
    health = await provider.health()
    assert health.healthy is True


@pytest.mark.asyncio
async def test_rate_limiter_is_exercised(client: httpx.AsyncClient, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    respx_mock.get("https://api.discogs.com/database/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    calls = []
    import muzilla.providers.discogs as discogs_module

    real_get_limiter = discogs_module.get_limiter

    def spy(name: str):
        calls.append(name)
        return real_get_limiter(name)

    monkeypatch.setattr(discogs_module, "get_limiter", spy)
    provider = DiscogsProvider(client, token="test-token")
    await provider.search_releases(ReleaseQuery(album="x"), limit=1)
    assert calls == ["discogs"]
