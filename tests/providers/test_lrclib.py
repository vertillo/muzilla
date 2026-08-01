from __future__ import annotations

import httpx
import pytest
import respx

from muzilla.providers.errors import ProviderTransientError
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
    assert lyrics is not None
    assert lyrics.text == "[00:01.00]synced version"
    assert lyrics.synced is True
    assert lyrics.source == "lrclib"

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
    assert lyrics is not None
    assert lyrics.text == "plain version"
    assert lyrics.synced is False


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
async def test_get_lyrics_retries_transient_http_errors(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get("https://lrclib.net/api/get").mock(
        side_effect=[httpx.Response(408), httpx.Response(503), httpx.Response(200, json={"plainLyrics": "recovered"})]
    )
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    provider = LrcLibProvider(
        client, max_attempts=3, base_backoff_seconds=0.1, sleep=sleep, random_value=lambda: 1.0
    )
    lyrics = await provider.get_lyrics("Artist", "Title", duration_ms=None)

    assert lyrics is not None
    assert lyrics.text == "recovered"
    assert route.call_count == 3
    assert delays == [0.1, 0.2]


@pytest.mark.asyncio
async def test_get_lyrics_honours_retry_after_for_rate_limits(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://lrclib.net/api/get").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(200, json={"plainLyrics": "recovered"}),
        ]
    )
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    provider = LrcLibProvider(
        client, max_attempts=2, base_backoff_seconds=0.1, sleep=sleep, random_value=lambda: 0.0
    )
    assert await provider.get_lyrics("Artist", "Title", duration_ms=None) is not None
    assert delays == [3.0]


@pytest.mark.asyncio
async def test_get_lyrics_timeout_exhausts_bounded_retry_budget(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("https://lrclib.net/api/get").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )

    async def sleep(_: float) -> None:
        return None

    provider = LrcLibProvider(client, max_attempts=3, sleep=sleep)
    with pytest.raises(ProviderTransientError, match="timed out"):
        await provider.get_lyrics("Artist", "Title", duration_ms=None)
    assert route.call_count == 3


@pytest.mark.asyncio
async def test_get_lyrics_marks_non_retryable_http_errors_permanent(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    from muzilla.providers.errors import ProviderPermanentError

    route = respx_mock.get("https://lrclib.net/api/get").mock(return_value=httpx.Response(401))
    provider = LrcLibProvider(client)
    with pytest.raises(ProviderPermanentError, match="HTTP 401"):
        await provider.get_lyrics("Artist", "Title", duration_ms=None)
    assert route.call_count == 1


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
