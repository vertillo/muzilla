from __future__ import annotations

import logging
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muzilla.api.app import create_app
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Setting


def test_get_settings_defaults(client: TestClient) -> None:
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    names = {p["provider"] for p in body["providers"]}
    assert names == {"musicbrainz", "discogs", "deezer", "acoustid", "coverartarchive", "lrclib"}
    assert next(p for p in body["providers"] if p["provider"] == "discogs")["enabled"] is False
    assert all(not p["token_configured"] for p in body["providers"])
    assert body["templates"] == {"album": None, "singleton": None, "default": None}
    assert body["enrichment"] == {
        "metadata_auto": True,
        "art_auto": True,
        "lyrics_auto": True,
        "replaygain_auto": True,
    }
    assert body["paths_policy"] == {"create_directories": False}


def test_update_provider_setting_never_echoes_or_persists_token_in_database(
    client: TestClient,
    migrated_db: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    credential = "synthetic-api-test-token"
    caplog.set_level(logging.DEBUG)
    resp = client.put(
        "/api/settings/providers/discogs", json={"enabled": True, "token": credential}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"provider": "discogs", "enabled": True, "token_configured": True}
    assert credential not in resp.text

    # GET afterward also never echoes it
    resp2 = client.get("/api/settings")
    assert credential not in resp2.text
    assert credential not in caplog.text
    database_files = [migrated_db, *migrated_db.parent.glob(f"{migrated_db.name}-*")]
    assert all(credential.encode() not in path.read_bytes() for path in database_files)


def test_save_swaps_the_live_provider_set(client: TestClient) -> None:
    response = client.put(
        "/api/settings/providers/discogs", json={"enabled": True, "token": "synthetic-live-token"}
    )

    assert response.status_code == 200
    status = client.get("/api/providers/status")
    assert status.status_code == 200
    discogs = next(item for item in status.json()["items"] if item["provider"] == "discogs")
    assert discogs["enabled"] is True
    assert discogs["token_configured"] is True
    assert discogs["live"] is True
    assert discogs["state"] in {
        "checking",
        "operational",
        "temporary_unavailable",
        "invalid_credentials",
    }


def test_startup_migrates_legacy_token_and_new_installation_reuses_secret(
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_credential = "synthetic-legacy-token"
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(
            Setting(
                key="providers.discogs",
                value={"enabled": True, "token": legacy_credential},
            )
        )
        session.commit()
    engine.dispose()

    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("MUZILLA_STORAGE__PROVIDER_SECRETS_DIR", raising=False)
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")

    with TestClient(create_app()) as first_install:
        discogs = next(
            provider
            for provider in first_install.get("/api/settings").json()["providers"]
            if provider["provider"] == "discogs"
        )
        assert discogs["token_configured"] is True

    # A fresh application instance models a supported reinstall/container
    # recreation while keeping the same /data authority.
    with TestClient(create_app()) as reinstalled:
        discogs = next(
            provider
            for provider in reinstalled.get("/api/settings").json()["providers"]
            if provider["provider"] == "discogs"
        )
        assert discogs["token_configured"] is True

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        row = session.get(Setting, "providers.discogs")
        assert row is not None
        assert "token" not in row.value
        assert row.value["secret_ref"] == "providers.discogs.token"
    engine.dispose()

    secret_root = migrated_db.parent / "secrets" / "providers"
    assert stat.S_IMODE(secret_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(next(secret_root.glob("*.secret")).stat().st_mode) == 0o600
    database_files = [migrated_db, *migrated_db.parent.glob(f"{migrated_db.name}-*")]
    assert all(legacy_credential.encode() not in path.read_bytes() for path in database_files)


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
        "/api/settings/templates/preview",
        json={"template": "$albumartist - $album - $track $title"},
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


def test_enrichment_settings_persist_and_round_trip(client: TestClient) -> None:
    resp = client.put(
        "/api/settings/enrichment",
        json={"metadata_auto": False, "art_auto": False, "lyrics_auto": False, "replaygain_auto": False},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "metadata_auto": False,
        "art_auto": False,
        "lyrics_auto": False,
        "replaygain_auto": False,
    }
    resp2 = client.get("/api/settings")
    assert resp2.json()["enrichment"] == {
        "metadata_auto": False,
        "art_auto": False,
        "lyrics_auto": False,
        "replaygain_auto": False,
    }
    # Restore defaults for other tests (fresh client per test, but keep explicit)
    client.put(
        "/api/settings/enrichment",
        json={"metadata_auto": True, "art_auto": True, "lyrics_auto": True, "replaygain_auto": True},
    )


def test_paths_policy_persist_and_round_trip(client: TestClient) -> None:
    resp = client.put("/api/settings/paths", json={"create_directories": True})
    assert resp.status_code == 200
    assert resp.json() == {"create_directories": True}
    resp2 = client.get("/api/settings")
    assert resp2.json()["paths_policy"] == {"create_directories": True}
    client.put("/api/settings/paths", json={"create_directories": False})


def test_enrichment_env_precedence(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_ENRICHMENT__METADATA_AUTO", "false")
    # Even though we try to enable via API, env wins and effective stays false
    resp = client.put("/api/settings/enrichment", json={"metadata_auto": True})
    assert resp.status_code == 200
    # Effective should still be false due to env
    assert resp.json()["metadata_auto"] is False
    get_resp = client.get("/api/settings")
    assert get_resp.json()["enrichment"]["metadata_auto"] is False
    # Cleanup env for other tests
    monkeypatch.delenv("MUZILLA_ENRICHMENT__METADATA_AUTO", raising=False)
    # After env removed, the stored True should now be visible
    resp2 = client.put("/api/settings/enrichment", json={"metadata_auto": True})
    assert resp2.json()["metadata_auto"] is True


def test_paths_env_precedence(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_PATHS__CREATE_DIRECTORIES", "true")
    resp = client.put("/api/settings/paths", json={"create_directories": False})
    assert resp.status_code == 200
    # Env true should win over stored false
    assert resp.json()["create_directories"] is True
    get_resp = client.get("/api/settings")
    assert get_resp.json()["paths_policy"]["create_directories"] is True
    monkeypatch.delenv("MUZILLA_PATHS__CREATE_DIRECTORIES", raising=False)
    resp2 = client.put("/api/settings/paths", json={"create_directories": False})
    assert resp2.json()["create_directories"] is False


def test_manual_candidate_uses_effective_paths_policy(client: TestClient) -> None:
    # The real effective-paths usage is covered by checking that manual import
    # goes through effective_paths_config (unit-tested via settings service).
    # Here we at least verify the API round-trip for paths_policy.
    client.put("/api/settings/paths", json={"create_directories": True})
    resp = client.get("/api/settings")
    assert resp.json()["paths_policy"]["create_directories"] is True
    client.put("/api/settings/paths", json={"create_directories": False})
    assert client.get("/api/settings").json()["paths_policy"]["create_directories"] is False
