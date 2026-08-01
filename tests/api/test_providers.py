from __future__ import annotations

from fastapi.testclient import TestClient

from muzilla.providers import status as provider_status


def test_get_providers_status_lists_every_known_provider(client: TestClient) -> None:
    resp = client.get("/api/providers/status")
    assert resp.status_code == 200
    body = resp.json()
    names = {item["provider"] for item in body["items"]}
    assert names == {"musicbrainz", "discogs", "deezer", "acoustid", "coverartarchive", "lrclib"}
    assert {item["state"] for item in body["items"]} <= {
        "disabled", "not_configured", "checking", "operational", "temporary_unavailable", "invalid_credentials"
    }


def test_get_providers_status_reflects_config_and_no_token(client: TestClient) -> None:
    resp = client.get("/api/providers/status")
    body = resp.json()
    by_name = {item["provider"]: item for item in body["items"]}

    # default test config disables discogs entirely
    assert by_name["discogs"]["enabled"] is False
    assert by_name["discogs"]["live"] is False

    # acoustid is enabled by default but no token is configured in tests
    assert by_name["acoustid"]["enabled"] is True
    assert by_name["acoustid"]["requires_auth"] is True
    assert by_name["acoustid"]["token_configured"] is False
    assert by_name["acoustid"]["live"] is False

    # musicbrainz needs no auth and is enabled by default
    assert by_name["musicbrainz"]["enabled"] is True
    assert by_name["musicbrainz"]["live"] is True


def test_test_connection_reports_not_configured_without_a_network_call(client: TestClient) -> None:
    response = client.post("/api/providers/discogs/test")

    assert response.status_code == 200
    assert response.json()["state"] == "disabled"


def test_get_providers_status_reflects_recorded_rate_limit(client: TestClient) -> None:
    provider_status._status.clear()
    provider_status.record_response("musicbrainz", 429)
    try:
        resp = client.get("/api/providers/status")
        body = resp.json()
        by_name = {item["provider"]: item for item in body["items"]}
        assert by_name["musicbrainz"]["rate_limited"] is True
    finally:
        provider_status._status.clear()
