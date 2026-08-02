from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from muzilla.providers.base import ProviderRef, ReleaseQuery
from muzilla.providers.musicbrainz import MusicBrainzProvider

FIXTURES = Path(__file__).parent.parent / "fixtures" / "providers" / "musicbrainz"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://musicbrainz.org/ws/2")


@pytest.mark.asyncio
async def test_search_releases_maps_fields(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://musicbrainz.org/ws/2/release").mock(
        return_value=httpx.Response(200, json=_load("search_releases.json"))
    )
    provider = MusicBrainzProvider(client)
    results = await provider.search_releases(ReleaseQuery(album="Ágætis byrjun", album_artist="Sigur Rós"), limit=5)

    assert len(results) == 1
    candidate = results[0]
    assert candidate.source == "musicbrainz"
    assert candidate.album == "Ágætis byrjun"
    assert candidate.album_artist == "Sigur Rós"
    assert candidate.year == 1999
    assert candidate.original_year == 1999
    assert candidate.label == "Fat Cat Records"
    assert candidate.catalog_number == "FAT007CD"
    assert candidate.barcode == "5028983207323"
    assert candidate.country == "IS"
    assert candidate.mb_release_id == "076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8"


@pytest.mark.asyncio
async def test_search_releases_uses_isrc_for_a_track_query(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    provider = MusicBrainzProvider(client)
    respx_mock.get("https://musicbrainz.org/ws/2/release").mock(
        return_value=httpx.Response(200, json={"releases": []})
    )
    results = await provider.search_releases(
        ReleaseQuery(title='A "Quoted" Song', artist="Artist", isrc="ISF029900001"), limit=5
    )
    assert results == []
    request = respx_mock.calls.last.request
    assert 'recording:"A \\"Quoted\\" Song"' in request.url.params["query"]
    assert 'artist:"Artist"' in request.url.params["query"]
    assert 'isrc:"ISF029900001"' in request.url.params["query"]


@pytest.mark.asyncio
async def test_get_release_parses_full_tracklist(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://musicbrainz.org/ws/2/release/076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8").mock(
        return_value=httpx.Response(200, json=_load("get_release.json"))
    )
    provider = MusicBrainzProvider(client)
    candidate = await provider.get_release(ProviderRef(provider="musicbrainz", id="076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8"))

    assert candidate is not None
    assert candidate.album == "Ágætis byrjun"
    assert len(candidate.tracks) == 2
    track1, track2 = candidate.tracks
    assert track1.position == 1
    assert track1.title == "Intro"
    assert track1.duration_ms == 97000
    assert track1.disc_number == 1
    assert track1.isrc == "ISF029900001"
    assert track1.mb_recording_id == "bb111111-0000-0000-0000-000000000001"
    assert track2.artist == "Sigur Rós"
    assert track2.duration_ms == 601000


@pytest.mark.asyncio
async def test_get_release_404_returns_none(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://musicbrainz.org/ws/2/release/does-not-exist").mock(
        return_value=httpx.Response(404)
    )
    provider = MusicBrainzProvider(client)
    result = await provider.get_release(ProviderRef(provider="musicbrainz", id="does-not-exist"))
    assert result is None


@pytest.mark.asyncio
async def test_get_release_5xx_propagates(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://musicbrainz.org/ws/2/release/x").mock(return_value=httpx.Response(503))
    provider = MusicBrainzProvider(client)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.get_release(ProviderRef(provider="musicbrainz", id="x"))


@pytest.mark.asyncio
async def test_health_ok(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://musicbrainz.org/ws/2/release").mock(
        return_value=httpx.Response(200, json={"releases": []})
    )
    provider = MusicBrainzProvider(client)
    health = await provider.health()
    assert health.healthy is True


@pytest.mark.asyncio
async def test_rate_limiter_is_exercised(client: httpx.AsyncClient, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    respx_mock.get("https://musicbrainz.org/ws/2/release").mock(
        return_value=httpx.Response(200, json={"releases": []})
    )
    calls = []
    import muzilla.providers.musicbrainz as mb_module

    real_get_limiter = mb_module.get_limiter

    def spy(name: str):
        calls.append(name)
        return real_get_limiter(name)

    monkeypatch.setattr(mb_module, "get_limiter", spy)
    provider = MusicBrainzProvider(client)
    await provider.search_releases(ReleaseQuery(album="x"), limit=1)
    assert calls == ["musicbrainz"]
