from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muzilla.api.app import create_app


@pytest.fixture
def auth_client(
    migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "true")
    monkeypatch.setenv("MUZILLA_AUTH__PASSWORD", "hunter2")
    monkeypatch.setenv("MUZILLA_AUTH__SESSION_SECRET", "test-secret")
    with TestClient(create_app()) as c:
        yield c


def test_protected_route_requires_auth(auth_client: TestClient) -> None:
    resp = auth_client.get("/api/tracks")
    assert resp.status_code == 401


def test_health_and_login_are_reachable_without_auth(auth_client: TestClient) -> None:
    assert auth_client.get("/api/health").status_code == 200
    assert auth_client.get("/api/auth/status").status_code == 200


def test_login_with_wrong_password_rejected(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/auth/login", json={"password": "wrong"})
    assert resp.status_code == 401


def test_login_then_access_protected_route(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/auth/login", json={"password": "hunter2"})
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "authenticated": True}

    resp = auth_client.get("/api/tracks")
    assert resp.status_code == 200


def test_status_reflects_login_state(auth_client: TestClient) -> None:
    assert auth_client.get("/api/auth/status").json()["authenticated"] is False

    auth_client.post("/api/auth/login", json={"password": "hunter2"})

    assert auth_client.get("/api/auth/status").json()["authenticated"] is True


def test_logout_revokes_access(auth_client: TestClient) -> None:
    auth_client.post("/api/auth/login", json={"password": "hunter2"})
    assert auth_client.get("/api/tracks").status_code == 200

    auth_client.post("/api/auth/logout")

    assert auth_client.get("/api/tracks").status_code == 401


def test_startup_fails_fast_when_auth_enabled_without_password(
    migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "true")
    monkeypatch.delenv("MUZILLA_AUTH__PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="password"), TestClient(create_app()):
        pass


def test_startup_fails_fast_when_auth_enabled_without_session_secret(
    migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "true")
    monkeypatch.setenv("MUZILLA_AUTH__PASSWORD", "hunter2")
    monkeypatch.delenv("MUZILLA_AUTH__SESSION_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SESSION_SECRET"), TestClient(create_app()):
        pass


def test_auth_disabled_allows_access_without_login(
    migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    with TestClient(create_app()) as client:
        assert client.get("/api/tracks").status_code == 200
        status = client.get("/api/auth/status").json()
        assert status == {"enabled": False, "authenticated": True}
