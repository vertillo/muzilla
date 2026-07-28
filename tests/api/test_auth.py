from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muzilla.api.app import create_app
from muzilla.api.routers.auth import _login_limiter


@pytest.fixture(autouse=True)
def _reset_login_limiter() -> None:
    # _login_limiter is module-level state shared across the whole test
    # session (every TestClient in this file arrives from the same
    # "testclient" host), so a rate-limit test earlier in the file would
    # otherwise poison a plain login test running later.
    _login_limiter._windows.clear()


@pytest.fixture
def auth_client(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
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
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    with TestClient(create_app()) as client:
        assert client.get("/api/tracks").status_code == 200
        status = client.get("/api/auth/status").json()
        assert status == {"enabled": False, "authenticated": True}


def test_correct_password_still_authenticates_after_rate_limit_change(
    auth_client: TestClient,
) -> None:
    # Guards the hash-once refactor: verify_password now takes a
    # precomputed hash from app.state rather than the config directly.
    resp = auth_client.post("/api/auth/login", json={"password": "hunter2"})
    assert resp.status_code == 200


def test_sixth_login_attempt_within_window_is_rate_limited(
    auth_client: TestClient,
) -> None:
    for _ in range(5):
        resp = auth_client.post("/api/auth/login", json={"password": "wrong"})
        assert resp.status_code == 401

    resp = auth_client.post("/api/auth/login", json={"password": "wrong"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) > 0


def test_rate_limit_window_expires_and_attempts_resume() -> None:
    # Exercised directly against the limiter rather than through the
    # router, which calls FixedWindowLimiter.check() with no `now`
    # override — this is the one behavior that needs simulated time.
    limiter = _login_limiter
    key = "window-expiry-test"
    for _ in range(5):
        assert limiter.check(key, now=0.0) is None
    assert limiter.check(key, now=0.0) is not None  # 6th attempt, same window

    assert limiter.check(key, now=61.0) is None  # new window, 61s later


def test_successful_login_resets_the_rate_limit_counter(
    auth_client: TestClient,
) -> None:
    for _ in range(4):
        auth_client.post("/api/auth/login", json={"password": "wrong"})

    resp = auth_client.post("/api/auth/login", json={"password": "hunter2"})
    assert resp.status_code == 200

    # If the counter hadn't reset, this 5th actual attempt plus the 4
    # priors would already be at the limit.
    for _ in range(4):
        resp = auth_client.post("/api/auth/login", json={"password": "wrong"})
        assert resp.status_code == 401
