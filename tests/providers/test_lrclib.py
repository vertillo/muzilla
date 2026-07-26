from __future__ import annotations

import httpx
import pytest
import respx

from muzilla.providers.lrclib import LrcLibProvider


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://lrclib.net/api")


@pytest.mark.asyncio
async def test_get_lyrics_prefers_synced(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://lrclib.net/api/get").mock(
        return_value=httpx.Response(
            200,
            json={
                "plainLyrics": "plain version",
                "syncedLyrics": "[00:01.00]synced version",
            },
        )
    )
    provider = LrcLibProvider(client)
    lyrics = await provider.get_lyrics("Sigur Rós", "Svefn-g-englar", duration_ms=601000)
    assert lyrics == "[00:01.00]synced version"

    request = respx_mock.calls.last.request
    assert request.url.params["artist_name"] == "Sigur Rós"
    assert request.url.params["track_name"] == "Svefn-g-englar"
    assert request.url.params["duration"] == "601"


@pytest.mark.asyncio
async def test_get_lyrics_falls_back_to_plain(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://lrclib.net/api/get").mock(
        return_value=httpx.Response(200, json={"plainLyrics": "plain version", "syncedLyrics": ""})
    )
    provider = LrcLibProvider(client)
    lyrics = await provider.get_lyrics("Artist", "Title", duration_ms=None)
    assert lyrics == "plain version"


@pytest.mark.asyncio
async def test_get_lyrics_no_match_returns_none(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://lrclib.net/api/get").mock(return_value=httpx.Response(200, json={}))
    provider = LrcLibProvider(client)
    lyrics = await provider.get_lyrics("Artist", "Title", duration_ms=None)
    assert lyrics is None


@pytest.mark.asyncio
async def test_get_lyrics_404_returns_none(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://lrclib.net/api/get").mock(return_value=httpx.Response(404))
    provider = LrcLibProvider(client)
    lyrics = await provider.get_lyrics("Artist", "Title", duration_ms=None)
    assert lyrics is None


@pytest.mark.asyncio
async def test_get_lyrics_5xx_propagates(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://lrclib.net/api/get").mock(return_value=httpx.Response(503))
    provider = LrcLibProvider(client)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.get_lyrics("Artist", "Title", duration_ms=None)


@pytest.mark.asyncio
async def test_health_ok(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://lrclib.net/api/get").mock(return_value=httpx.Response(404))
    provider = LrcLibProvider(client)
    health = await provider.health()
    assert health.healthy is True


@pytest.mark.asyncio
async def test_rate_limiter_is_exercised(client: httpx.AsyncClient, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    respx_mock.get("https://lrclib.net/api/get").mock(return_value=httpx.Response(404))
    calls = []
    import muzilla.providers.lrclib as lrclib_module

    real_get_limiter = lrclib_module.get_limiter

    def spy(name: str):
        calls.append(name)
        return real_get_limiter(name)

    monkeypatch.setattr(lrclib_module, "get_limiter", spy)
    provider = LrcLibProvider(client)
    await provider.get_lyrics("Artist", "Title", duration_ms=None)
    assert calls == ["lrclib"]
