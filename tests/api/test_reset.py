from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from muzilla.api.app import create_app
from muzilla.api.routers import settings as settings_router
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import AdminOperation, Job, Setting, Track
from muzilla.services.secrets import FileSecretStore


@pytest.fixture
def reset_client(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Path, Path]]:
    library = tmp_path / "isolated-music"
    music = library / "fixture.flac"
    library.mkdir()
    music.write_bytes(b"isolated reset API fixture")
    data = tmp_path / "isolated-data"
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__DATA_DIR", str(data))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(data / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(data / "blobs"))
    monkeypatch.setenv("MUZILLA_STORAGE__PROVIDER_SECRETS_DIR", str(data / "secrets/providers"))
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(library))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "true")
    monkeypatch.setenv("MUZILLA_AUTH__PASSWORD", "hunter2")
    monkeypatch.setenv("MUZILLA_AUTH__SESSION_SECRET", "reset-test-session-secret")

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    secret_store = FileSecretStore(data / "secrets/providers")
    secret_ref = "providers.discogs.token.reset-api"
    secret_store.set(secret_ref, "isolated-api-secret")
    with factory() as session:
        session.add_all(
            [
                Track(
                    path=str(music),
                    filename=music.name,
                    ext=music.suffix,
                    size_bytes=music.stat().st_size,
                    mtime_ns=music.stat().st_mtime_ns,
                ),
                Job(type="scan", payload={"root": str(library)}),
                Setting(
                    key="providers.discogs",
                    value={"enabled": True, "secret_ref": secret_ref},
                ),
            ]
        )
        session.commit()
    engine.dispose()

    with TestClient(create_app()) as client:
        login = client.post("/api/auth/login", json={"password": "hunter2"})
        assert login.status_code == 200
        yield client, music, data


def _csrf_headers(client: TestClient, *, key: str) -> dict[str, str]:
    status = client.get("/api/auth/status").json()
    return {
        "Origin": "http://testserver",
        "X-CSRF-Token": status["csrf_token"],
        "Idempotency-Key": key,
    }


def test_reset_rejects_missing_or_cross_origin_and_missing_csrf(
    reset_client: tuple[TestClient, Path, Path],
) -> None:
    client, _, _ = reset_client
    body = {"scope": "catalog_and_activity", "confirmation": "RESET CATALOG AND ACTIVITY"}
    token = client.get("/api/auth/status").json()["csrf_token"]

    assert client.post(
        "/api/settings/reset/catalog", json=body, headers={"Idempotency-Key": "a"}
    ).status_code == 403
    assert client.post(
        "/api/settings/reset/catalog",
        json=body,
        headers={"Origin": "https://evil.example", "X-CSRF-Token": token, "Idempotency-Key": "b"},
    ).status_code == 403
    assert client.post(
        "/api/settings/reset/catalog",
        json=body,
        headers={"Origin": "http://testserver", "Idempotency-Key": "c"},
    ).status_code == 403


def test_settings_secret_apply_and_cover_upload_require_origin_and_csrf(
    reset_client: tuple[TestClient, Path, Path],
) -> None:
    client, _, _ = reset_client
    assert client.put(
        "/api/settings/providers/discogs", json={"enabled": False}
    ).status_code == 403
    assert client.post(
        "/api/reviews/999/apply", headers={"Idempotency-Key": "apply-no-csrf"}
    ).status_code == 403
    assert client.post(
        "/api/reviews/999/cover/candidates",
        content=b"not-an-image",
        headers={"Content-Type": "image/jpeg"},
    ).status_code == 403


def test_catalog_reset_is_audited_idempotent_and_preserves_music_settings_and_secret(
    reset_client: tuple[TestClient, Path, Path], migrated_db: Path
) -> None:
    client, music, data = reset_client
    before = hashlib.sha256(music.read_bytes()).hexdigest()
    headers = _csrf_headers(client, key="catalog-api-reset")
    body = {"scope": "catalog_and_activity", "confirmation": "RESET CATALOG AND ACTIVITY"}

    response = client.post("/api/settings/reset/catalog", json=body, headers=headers)
    assert response.status_code == 200
    result = response.json()
    assert result["scope"] == "catalog_and_activity"
    assert result["state"] == "succeeded"
    assert result["settings_preserved"] is True
    assert result["secrets_preserved"] is True
    assert result["music_files_touched"] is False
    assert hashlib.sha256(music.read_bytes()).hexdigest() == before

    replay = client.post("/api/settings/reset/catalog", json=body, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["operation_id"] == result["operation_id"]

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Track)) == 0
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.get(Setting, "providers.discogs") is not None
        operation = session.get(AdminOperation, result["operation_id"])
        assert operation is not None
        assert operation.actor == "single-user"
        assert "hunter2" not in str(operation.outcome)
        assert str(music) not in str(operation.outcome)
    engine.dispose()
    assert FileSecretStore(data / "secrets/providers").get(
        "providers.discogs.token.reset-api"
    ) == "isolated-api-secret"


@pytest.mark.asyncio
async def test_api_quiesce_recovers_expired_external_lease_but_waits_for_active_lease(
    reset_client: tuple[TestClient, Path, Path], migrated_db: Path
) -> None:
    client, _, _ = reset_client
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        settings_router.reset_service.prepare_reset(
            session,
            scope=settings_router.reset_service.ResetScope.CATALOG_AND_ACTIVITY,
            idempotency_key="api-expired-external-lease",
        )
        external_apply = Job(
            type="apply_review_bundle",
            state="cancelling",
            payload={"apply_run_id": 42},
            worker_id="external-cli",
            cancel_requested=True,
            lease_until=datetime.now(UTC) + timedelta(hours=1),
        )
        expired_apply = Job(
            type="apply_review_bundle",
            state="cancelling",
            payload={"apply_run_id": 43},
            worker_id="dead-external-cli",
            cancel_requested=True,
            lease_until=datetime.now(UTC) - timedelta(seconds=1),
        )
        session.add_all([external_apply, expired_apply])
        session.commit()
        external_apply_id = external_apply.id
        expired_apply_id = expired_apply.id

    wait_task = asyncio.create_task(
        settings_router._wait_for_cross_process_quiesce(client.app.state.config)
    )
    for _ in range(20):
        with factory() as session:
            expired = session.get(Job, expired_apply_id)
            assert expired is not None
            if expired.state == "cancelled":
                assert expired.worker_id is None
                assert expired.lease_until is None
                break
        await asyncio.sleep(client.app.state.config.jobs.cancel_poll_seconds)
    else:
        pytest.fail("expired external lease was not recovered within the polling bound")

    assert wait_task.done() is False
    with factory() as session:
        persisted = session.get(Job, external_apply_id)
        assert persisted is not None
        assert persisted.state == "cancelling"
        persisted.state = "cancelled"
        session.commit()

    await asyncio.wait_for(wait_task, timeout=1)
    engine.dispose()


def test_factory_reset_requires_exact_scope_phrase_and_current_password_then_revokes_session(
    reset_client: tuple[TestClient, Path, Path], migrated_db: Path
) -> None:
    client, music, data = reset_client
    before = hashlib.sha256(music.read_bytes()).hexdigest()
    url = "/api/settings/reset/factory"
    headers = _csrf_headers(client, key="factory-api-reset")

    wrong_phrase = client.post(
        url,
        headers=headers,
        json={"scope": "factory", "confirmation": "factory reset", "password": "hunter2"},
    )
    assert wrong_phrase.status_code == 422
    wrong_password = client.post(
        url,
        headers={**headers, "Idempotency-Key": "factory-wrong-password"},
        json={"scope": "factory", "confirmation": "FACTORY RESET MUZILLA", "password": "wrong"},
    )
    assert wrong_password.status_code == 403

    response = client.post(
        url,
        headers=headers,
        json={"scope": "factory", "confirmation": "FACTORY RESET MUZILLA", "password": "hunter2"},
    )
    assert response.status_code == 200
    assert response.json()["settings_preserved"] is False
    assert response.json()["secrets_preserved"] is False
    assert hashlib.sha256(music.read_bytes()).hexdigest() == before
    assert client.get("/api/tracks").status_code == 401

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Setting)) == 0
    engine.dispose()
    assert FileSecretStore(data / "secrets/providers").get(
        "providers.discogs.token.reset-api"
    ) is None


def test_factory_reset_retry_repairs_runtime_after_post_cleanup_refresh_failure(
    reset_client: tuple[TestClient, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = reset_client
    real_build_provider_set = settings_router.providers_service.build_provider_set
    refresh_attempts = 0

    def fail_first_refresh(config: object) -> object:
        nonlocal refresh_attempts
        refresh_attempts += 1
        if refresh_attempts == 1:
            raise RuntimeError("synthetic post-reset runtime refresh failure")
        return real_build_provider_set(config)  # type: ignore[arg-type]

    monkeypatch.setattr(
        settings_router.providers_service,
        "build_provider_set",
        fail_first_refresh,
    )
    body = {
        "scope": "factory",
        "confirmation": "FACTORY RESET MUZILLA",
        "password": "hunter2",
    }
    headers = _csrf_headers(client, key="factory-runtime-recovery")
    old_snapshot = client.app.state.provider_runtime.acquire()
    old_discogs_client = old_snapshot.provider_set.metadata["discogs"]._client

    failed = client.post("/api/settings/reset/factory", json=body, headers=headers)
    assert failed.status_code == 503
    assert failed.json()["detail"] == "factory runtime refresh incomplete; retry is required"

    worker_task = client.app.state.worker_controller.task
    assert worker_task is not None and worker_task.done()
    revoked_snapshot = client.app.state.provider_runtime.acquire()
    assert revoked_snapshot.provider_set.clients == ()
    assert "discogs" not in revoked_snapshot.provider_set.metadata
    assert revoked_snapshot.config is not None
    assert revoked_snapshot.config.providers.discogs.resolved_token() is None
    assert old_discogs_client.is_closed is True
    assert client.get("/api/auth/status").json()["authenticated"] is False

    engine = create_db_engine(client.app.state.config.storage.db_path)
    factory = create_session_factory(engine)
    with factory() as session:
        operation = session.scalar(
            select(AdminOperation).where(
                AdminOperation.idempotency_key == "factory-runtime-recovery"
            )
        )
        assert operation is not None
        assert operation.state == "running"
        assert operation.phase == "storage_cleaned"
    engine.dispose()

    assert client.post("/api/auth/login", json={"password": "hunter2"}).status_code == 200
    blocked_setting = client.put(
        "/api/settings/providers/discogs",
        json={"enabled": True, "token": "must-not-be-persisted"},
        headers=_csrf_headers(client, key="blocked-during-runtime-recovery"),
    )
    assert blocked_setting.status_code == 503
    replay = client.post(
        "/api/settings/reset/factory",
        json=body,
        headers=_csrf_headers(client, key="factory-runtime-recovery"),
    )
    assert replay.status_code == 200
    assert replay.json()["state"] == "succeeded"
    assert refresh_attempts == 2
    restarted_task = client.app.state.worker_controller.task
    assert restarted_task is not None and not restarted_task.done()
    client.portal.call(old_snapshot.release)
    client.portal.call(revoked_snapshot.release)


def test_successful_factory_reset_replay_does_not_revoke_runtime_or_restart_worker(
    reset_client: tuple[TestClient, Path, Path],
) -> None:
    client, _, _ = reset_client
    body = {
        "scope": "factory",
        "confirmation": "FACTORY RESET MUZILLA",
        "password": "hunter2",
    }
    key = "factory-success-replay"
    first = client.post(
        "/api/settings/reset/factory",
        json=body,
        headers=_csrf_headers(client, key=key),
    )
    assert first.status_code == 200
    assert client.post("/api/auth/login", json={"password": "hunter2"}).status_code == 200

    runtime = client.app.state.provider_runtime
    published_lease = runtime.acquire()
    published_set = published_lease.provider_set
    published_clients = published_set.clients
    assert published_clients
    published_generation = published_lease.generation
    worker_task = client.app.state.worker_controller.task
    assert worker_task is not None and not worker_task.done()

    replay = client.post(
        "/api/settings/reset/factory",
        json=body,
        headers=_csrf_headers(client, key=key),
    )

    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert runtime.current() is published_set
    current_lease = runtime.acquire()
    assert current_lease.generation == published_generation
    assert all(client_.is_closed is False for client_ in published_clients)
    assert client.app.state.worker_controller.task is worker_task
    assert worker_task.done() is False

    client.portal.call(published_lease.release)
    client.portal.call(current_lease.release)
