from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_metrics_disabled_by_default(client: TestClient) -> None:
    resp = client.get("/api/metrics")
    assert resp.status_code == 404


def test_metrics_enabled_returns_prometheus_exposition_format(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Builds its own TestClient after setting MUZILLA_METRICS__ENABLED,
    same reasoning as tests/api/test_changesets.py's backup-mode test:
    Config is loaded once at app-lifespan startup, so the env var must
    be set before TestClient(create_app()) is constructed, not after."""
    from muzilla.api.app import create_app

    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    monkeypatch.setenv("MUZILLA_METRICS__ENABLED", "true")

    with TestClient(create_app()) as client:
        resp = client.get("/api/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "# HELP muzilla_tracks " in body
    assert "muzilla_tracks 0" in body


def test_metrics_endpoint_needs_no_auth_when_enabled(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unauthenticated by default -- proven here with
    auth actually enabled, confirming /api/metrics isn't behind
    require_auth the way most routers are."""
    from muzilla.api.app import create_app

    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "true")
    monkeypatch.setenv("MUZILLA_AUTH__PASSWORD", "test-password-value")
    monkeypatch.setenv("MUZILLA_AUTH__SESSION_SECRET", "test-session-secret-value")
    monkeypatch.setenv("MUZILLA_METRICS__ENABLED", "true")

    with TestClient(create_app()) as client:
        resp = client.get("/api/metrics")  # no login, no cookie

    assert resp.status_code == 200
