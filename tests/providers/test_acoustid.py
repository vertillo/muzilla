from __future__ import annotations

import httpx
import pytest
import respx

from muzilla.providers.acoustid import AcoustIDProvider


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://api.acoustid.org/v2")


@respx.mock
async def test_lookup_returns_matches_from_a_successful_response(client: httpx.AsyncClient) -> None:
    respx.get("https://api.acoustid.org/v2/lookup").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "results": [
                    {
                        "score": 0.95,
                        "recordings": [
                            {
                                "id": "recording-1",
                                "releases": [{"id": "release-1"}, {"id": "release-2"}],
                            }
                        ],
                    }
                ],
            },
        )
    )
    provider = AcoustIDProvider(client, api_key="test-key")
    matches = await provider.lookup("AQAB...fingerprint", duration_s=180.0)
    assert len(matches) == 1
    assert matches[0].mb_recording_id == "recording-1"
    assert matches[0].mb_release_ids == ("release-1", "release-2")
    assert matches[0].score == 0.95


@respx.mock
async def test_lookup_with_no_results_returns_empty_list(client: httpx.AsyncClient) -> None:
    respx.get("https://api.acoustid.org/v2/lookup").mock(
        return_value=httpx.Response(200, json={"status": "ok", "results": []})
    )
    provider = AcoustIDProvider(client, api_key="test-key")
    matches = await provider.lookup("fingerprint", duration_s=180.0)
    assert matches == []


@respx.mock
async def test_lookup_skips_recordings_missing_an_id(client: httpx.AsyncClient) -> None:
    respx.get("https://api.acoustid.org/v2/lookup").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "results": [{"score": 0.5, "recordings": [{"releases": []}]}],
            },
        )
    )
    provider = AcoustIDProvider(client, api_key="test-key")
    matches = await provider.lookup("fingerprint", duration_s=180.0)
    assert matches == []


@respx.mock
async def test_lookup_error_status_returns_empty_list(client: httpx.AsyncClient) -> None:
    respx.get("https://api.acoustid.org/v2/lookup").mock(
        return_value=httpx.Response(200, json={"status": "error", "error": {"message": "bad"}})
    )
    provider = AcoustIDProvider(client, api_key="test-key")
    matches = await provider.lookup("fingerprint", duration_s=180.0)
    assert matches == []


@respx.mock
async def test_lookup_http_error_returns_empty_list(client: httpx.AsyncClient) -> None:
    respx.get("https://api.acoustid.org/v2/lookup").mock(return_value=httpx.Response(500))
    provider = AcoustIDProvider(client, api_key="test-key")
    matches = await provider.lookup("fingerprint", duration_s=180.0)
    assert matches == []


async def test_lookup_without_api_key_raises() -> None:
    provider = AcoustIDProvider(httpx.AsyncClient(), api_key=None)
    with pytest.raises(RuntimeError, match="API key"):
        await provider.lookup("fingerprint", duration_s=180.0)


async def test_health_without_api_key_is_unhealthy() -> None:
    provider = AcoustIDProvider(httpx.AsyncClient(), api_key=None)
    health = await provider.health()
    assert health.healthy is False


@respx.mock
async def test_health_with_api_key_makes_a_real_authenticated_probe(client: httpx.AsyncClient) -> None:
    route = respx.get("https://api.acoustid.org/v2/lookup").mock(
        return_value=httpx.Response(200, json={"status": "ok", "results": []})
    )
    provider = AcoustIDProvider(client, api_key="test-key")
    health = await provider.health()
    assert health.healthy is True
    assert route.called


@respx.mock
async def test_health_with_rejected_token_is_unhealthy(client: httpx.AsyncClient) -> None:
    respx.get("https://api.acoustid.org/v2/lookup").mock(return_value=httpx.Response(401))
    provider = AcoustIDProvider(client, api_key="test-key")

    health = await provider.health()

    assert health.healthy is False
    assert health.detail == "HTTP 401"
