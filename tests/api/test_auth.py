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
    assert resp.json()["enabled"] is True
    assert resp.json()["authenticated"] is True
    assert len(resp.json()["csrf_token"]) == 64

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
        assert status["enabled"] is False
        assert status["authenticated"] is True
        assert len(status["csrf_token"]) == 64


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


@pytest.fixture
def secure_cookie_client(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "true")
    monkeypatch.setenv("MUZILLA_AUTH__PASSWORD", "hunter2")
    monkeypatch.setenv("MUZILLA_AUTH__SESSION_SECRET", "test-secret")
    monkeypatch.setenv("MUZILLA_AUTH__COOKIE_SECURE", "true")
    with TestClient(create_app()) as c:
        yield c


def test_cookie_secure_defaults_to_false(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/auth/login", json={"password": "hunter2"})
    set_cookie = resp.headers.get_list("set-cookie")[0]
    assert "Secure" not in set_cookie


def test_cookie_secure_true_sets_the_secure_flag(
    secure_cookie_client: TestClient,
) -> None:
    resp = secure_cookie_client.post("/api/auth/login", json={"password": "hunter2"})
    set_cookie = resp.headers.get_list("set-cookie")[0]
    assert "Secure" in set_cookie


def test_logout_mirrors_cookie_secure_flag(secure_cookie_client: TestClient) -> None:
    secure_cookie_client.post("/api/auth/login", json={"password": "hunter2"})
    resp = secure_cookie_client.post("/api/auth/logout")
    set_cookie = resp.headers.get_list("set-cookie")[0]
    assert "Secure" in set_cookie


def test_logout_omits_secure_flag_when_cookie_secure_is_false(
    auth_client: TestClient,
) -> None:
    auth_client.post("/api/auth/login", json={"password": "hunter2"})
    resp = auth_client.post("/api/auth/logout")
    set_cookie = resp.headers.get_list("set-cookie")[0]
    assert "Secure" not in set_cookie


def test_cookie_captured_before_logout_is_rejected_after(
    auth_client: TestClient,
) -> None:
    # Simulates a copy of the session cookie captured before logout
    # (e.g. exfiltrated some other way) — deleting the client-side
    # cookie alone would leave this copy valid for the rest of its
    # 30-day TTL.
    auth_client.post("/api/auth/login", json={"password": "hunter2"})
    captured_cookie = auth_client.cookies["muzilla_session"]

    auth_client.post("/api/auth/logout")

    resp = auth_client.get("/api/tracks", cookies={"muzilla_session": captured_cookie})
    assert resp.status_code == 401


def test_login_after_logout_still_works(auth_client: TestClient) -> None:
    auth_client.post("/api/auth/login", json={"password": "hunter2"})
    auth_client.post("/api/auth/logout")

    resp = auth_client.post("/api/auth/login", json={"password": "hunter2"})
    assert resp.status_code == 200

    assert auth_client.get("/api/tracks").status_code == 200


def test_status_reflects_revocation_for_a_captured_cookie(
    auth_client: TestClient,
) -> None:
    auth_client.post("/api/auth/login", json={"password": "hunter2"})
    captured_cookie = auth_client.cookies["muzilla_session"]

    auth_client.post("/api/auth/logout")

    resp = auth_client.get(
        "/api/auth/status", cookies={"muzilla_session": captured_cookie}
    )
    assert resp.json()["authenticated"] is False
