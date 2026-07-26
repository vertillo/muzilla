from __future__ import annotations

import httpx
import pytest
import respx

from muzilla.providers.base import ProviderRef
from muzilla.providers.coverartarchive import CoverArtArchiveProvider

MBID = "076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8"


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://coverartarchive.org")


@pytest.mark.asyncio
async def test_get_art_maps_images(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"https://coverartarchive.org/release/{MBID}").mock(
        return_value=httpx.Response(
            200,
            json={
                "images": [
                    {
                        "image": "https://coverartarchive.org/release/076ad.../front.jpg",
                        "thumbnails": {"250": "https://coverartarchive.org/release/076ad.../front-250.jpg"},
                        "types": ["Front"],
                        "approved": True,
                    },
                    {
                        "image": "https://coverartarchive.org/release/076ad.../back.jpg",
                        "thumbnails": {"250": "https://coverartarchive.org/release/076ad.../back-250.jpg"},
                        "types": ["Back"],
                        "approved": True,
                    },
                ]
            },
        )
    )
    provider = CoverArtArchiveProvider(client)
    art = await provider.get_art(ProviderRef(provider="musicbrainz", id=MBID))

    assert len(art) == 2
    assert art[0].url == "https://coverartarchive.org/release/076ad.../front.jpg"
    assert art[0].source == "coverartarchive"
    assert art[0].width is None
    assert art[0].height is None
    assert art[0].mime is None


@pytest.mark.asyncio
async def test_get_art_404_returns_empty_list(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"https://coverartarchive.org/release/{MBID}").mock(return_value=httpx.Response(404))
    provider = CoverArtArchiveProvider(client)
    art = await provider.get_art(ProviderRef(provider="musicbrainz", id=MBID))
    assert art == []


@pytest.mark.asyncio
async def test_get_art_5xx_propagates(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"https://coverartarchive.org/release/{MBID}").mock(return_value=httpx.Response(503))
    provider = CoverArtArchiveProvider(client)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.get_art(ProviderRef(provider="musicbrainz", id=MBID))


@pytest.mark.asyncio
async def test_health_ok(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.head("https://coverartarchive.org/").mock(return_value=httpx.Response(200))
    provider = CoverArtArchiveProvider(client)
    health = await provider.health()
    assert health.healthy is True


@pytest.mark.asyncio
async def test_rate_limiter_is_exercised(client: httpx.AsyncClient, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    respx_mock.get(f"https://coverartarchive.org/release/{MBID}").mock(
        return_value=httpx.Response(200, json={"images": []})
    )
    calls = []
    import muzilla.providers.coverartarchive as caa_module

    real_get_limiter = caa_module.get_limiter

    def spy(name: str):
        calls.append(name)
        return real_get_limiter(name)

    monkeypatch.setattr(caa_module, "get_limiter", spy)
    provider = CoverArtArchiveProvider(client)
    await provider.get_art(ProviderRef(provider="musicbrainz", id=MBID))
    assert calls == ["coverartarchive"]
