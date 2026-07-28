from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_settings_defaults(client: TestClient) -> None:
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    names = {p["provider"] for p in body["providers"]}
    assert names == {"musicbrainz", "discogs", "deezer", "acoustid", "coverartarchive", "lrclib"}
    assert all(not p["token_configured"] for p in body["providers"])
    assert body["templates"] == {"album": None, "singleton": None, "default": None}


def test_update_provider_setting_never_echoes_token_back(client: TestClient) -> None:
    resp = client.put(
        "/api/settings/providers/discogs", json={"enabled": True, "token": "a-real-secret-value"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"provider": "discogs", "enabled": True, "token_configured": True}
    assert "a-real-secret-value" not in resp.text

    # GET afterward also never echoes it
    resp2 = client.get("/api/settings")
    assert "a-real-secret-value" not in resp2.text


def test_update_provider_setting_unknown_provider_400s(client: TestClient) -> None:
    resp = client.put("/api/settings/providers/spotify", json={"enabled": True})
    assert resp.status_code == 400


def test_update_templates_round_trips(client: TestClient) -> None:
    resp = client.put("/api/settings/templates", json={"album": "$album/$title"})
    assert resp.status_code == 200
    assert resp.json()["album"] == "$album/$title"

    resp2 = client.get("/api/settings")
    assert resp2.json()["templates"]["album"] == "$album/$title"


def test_update_strip_fields_round_trips(client: TestClient) -> None:
    resp = client.put("/api/settings/strip-fields", json={"fields": ["comment"]})
    assert resp.status_code == 200
    assert resp.json() == ["comment"]

    resp2 = client.get("/api/settings")
    assert resp2.json()["strip_fields"] == ["comment"]


def test_update_strip_fields_unknown_field_400s(client: TestClient) -> None:
    resp = client.put("/api/settings/strip-fields", json={"fields": ["not_a_real_field"]})
    assert resp.status_code == 400


def test_preview_template_endpoint(client: TestClient) -> None:
    resp = client.post(
        "/api/settings/templates/preview", json={"template": "$albumartist - $album - $track $title"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == []
    assert "Sigur Rós" in body["path"]


def test_preview_template_endpoint_returns_structural_error(client: TestClient) -> None:
    resp = client.post("/api/settings/templates/preview", json={"template": "%bogus{$title}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] != []
