from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from muzilla.providers.base import ProviderRef, ReleaseQuery
from muzilla.providers.deezer import DeezerProvider

FIXTURES = Path(__file__).parent.parent / "fixtures" / "providers" / "deezer"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://api.deezer.com")


@pytest.mark.asyncio
async def test_search_releases_maps_fields(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.deezer.com/search/album").mock(
        return_value=httpx.Response(200, json=_load("search_albums.json"))
    )
    provider = DeezerProvider(client)
    results = await provider.search_releases(ReleaseQuery(album="Ágætis byrjun", artist="Sigur Rós"), limit=5)

    assert len(results) == 1
    candidate = results[0]
    assert candidate.source == "deezer"
    assert candidate.album == "Ágætis byrjun"
    assert candidate.album_artist == "Sigur Rós"
    assert candidate.year == 1999
    assert candidate.deezer_album_id == "302127"


@pytest.mark.asyncio
async def test_search_releases_ignores_barcode_and_catno(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://api.deezer.com/search/album").mock(
        return_value=httpx.Response(200, json=_load("search_albums.json"))
    )
    provider = DeezerProvider(client)
    results = await provider.search_releases(
        ReleaseQuery(album="Ágætis byrjun", barcode="5028983207323", catalog_number="FAT007CD"), limit=5
    )
    assert len(results) == 1


@pytest.mark.asyncio
async def test_search_releases_with_no_usable_fields_returns_empty(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    provider = DeezerProvider(client)
    results = await provider.search_releases(ReleaseQuery(barcode="123"), limit=5)
    assert results == []
    assert respx_mock.calls.call_count == 0


@pytest.mark.asyncio
async def test_track_search_returns_release_summary_with_declared_count(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://api.deezer.com/search/track").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 42,
                        "title": "Twilight Twilight",
                        "duration": 241,
                        "track_position": 3,
                        "artist": {"name": "Piki"},
                        "album": {"id": 700, "title": "Twilight", "nb_tracks": 12},
                    }
                ]
            },
        )
    )
    provider = DeezerProvider(client)
    results = await provider.search_releases(
        ReleaseQuery(title="Twilight Twilight", artist="Piki"), limit=5
    )
    assert len(results) == 1
    candidate = results[0]
    assert candidate.ref.id == "700"
    assert candidate.candidate_type == "track"
    assert candidate.track_count == 12
    assert candidate.tracks == ()
    assert candidate.representative_track is not None
    assert candidate.representative_track.duration_ms == 241000


@pytest.mark.asyncio
async def test_get_release_parses_tracklist_and_original_year_left_none(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://api.deezer.com/album/302127").mock(
        return_value=httpx.Response(200, json=_load("get_album.json"))
    )
    provider = DeezerProvider(client)
    candidate = await provider.get_release(ProviderRef(provider="deezer", id="302127"))

    assert candidate is not None
    assert candidate.album == "Ágætis byrjun"
    assert candidate.year == 1999
    assert candidate.original_year is None
    assert candidate.barcode == "5028983207323"
    assert len(candidate.tracks) == 2
    track1, track2 = candidate.tracks
    assert track1.position == 1
    assert track1.title == "Intro"
    assert track1.duration_ms == 97000
    assert track1.isrc is None
    assert track2.duration_ms == 601000


@pytest.mark.asyncio
async def test_get_release_404_returns_none(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.deezer.com/album/999999").mock(return_value=httpx.Response(404))
    provider = DeezerProvider(client)
    result = await provider.get_release(ProviderRef(provider="deezer", id="999999"))
    assert result is None


@pytest.mark.asyncio
async def test_get_release_deezer_error_payload_returns_none(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    """Deezer returns 200 with an `error` object for a bad id instead of a real 404."""
    respx_mock.get("https://api.deezer.com/album/0").mock(
        return_value=httpx.Response(200, json={"error": {"type": "DataException", "message": "no data"}})
    )
    provider = DeezerProvider(client)
    result = await provider.get_release(ProviderRef(provider="deezer", id="0"))
    assert result is None


@pytest.mark.asyncio
async def test_get_track_candidate_resolves_track_id_then_hydrates_its_album(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://api.deezer.com/track/3135556").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 3135556,
                "title": "Starálfur",
                "duration": 406,
                "track_position": 2,
                "disk_number": 1,
                "artist": {"name": "Sigur Rós"},
                "album": {"id": 302127, "title": "Ágætis byrjun", "nb_tracks": 10},
            },
        )
    )
    respx_mock.get("https://api.deezer.com/album/302127").mock(
        return_value=httpx.Response(200, json=_load("get_album.json"))
    )
    provider = DeezerProvider(client)

    candidate = await provider.get_track_candidate(
        ProviderRef(provider="deezer", id="3135556")
    )

    assert candidate is not None
    assert candidate.ref == ProviderRef(provider="deezer", id="302127")
    assert candidate.candidate_type == "track"
    assert candidate.representative_track is not None
    assert candidate.representative_track.title == "Starálfur"


@pytest.mark.asyncio
async def test_health_ok(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.deezer.com/search/album").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    provider = DeezerProvider(client)
    health = await provider.health()
    assert health.healthy is True


@pytest.mark.asyncio
async def test_rate_limiter_is_exercised(client: httpx.AsyncClient, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    respx_mock.get("https://api.deezer.com/search/album").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    calls = []
    import muzilla.providers.deezer as deezer_module

    real_get_limiter = deezer_module.get_limiter

    def spy(name: str):
        calls.append(name)
        return real_get_limiter(name)

    monkeypatch.setattr(deezer_module, "get_limiter", spy)
    provider = DeezerProvider(client)
    await provider.search_releases(ReleaseQuery(album="x"), limit=1)
    assert calls == ["deezer"]
