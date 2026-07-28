from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muzilla.db.engine import create_db_engine, create_session_factory


def test_get_import_config_returns_configured_library_root(client: TestClient) -> None:
    resp = client.get("/api/imports/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["library_root"] == "/music"
    # /music does not exist on the machine running this test (only
    # inside the Docker image) -- the fixture never overrides it.
    assert body["library_root_exists"] is False


def test_get_import_config_reports_existing_library_root(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Builds its own TestClient after setting the env var, same
    # reasoning as test_metrics.py's backup-mode test: Config is loaded
    # once at app-lifespan startup, so MUZILLA_STORAGE__LIBRARY_ROOT
    # must be set before TestClient(create_app()) is constructed, not
    # after (the shared `client` fixture already started its app by
    # the time a test body runs).
    from muzilla.api.app import create_app

    library_dir = tmp_path / "library"
    library_dir.mkdir()
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(library_dir))

    with TestClient(create_app()) as client:
        resp = client.get("/api/imports/config")

    assert resp.status_code == 200
    body = resp.json()
    assert body["library_root"] == str(library_dir)
    assert body["library_root_exists"] is True


def test_post_scan_enqueues_job(client: TestClient) -> None:
    resp = client.post("/api/scan", json={"root": "/music"})
    assert resp.status_code == 202
    assert "job_id" in resp.json()


def test_post_scan_rejects_path_outside_library_root(client: TestClient) -> None:
    resp = client.post("/api/scan", json={"root": "/etc"})
    assert resp.status_code == 400


def test_post_imports_rejects_path_outside_library_root(client: TestClient) -> None:
    resp = client.post("/api/imports", json={"library_root": "/etc"})
    assert resp.status_code == 400


def test_start_import_creates_session(client: TestClient, migrated_db: Path) -> None:
    resp = client.post("/api/imports", json={"library_root": "/music"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["library_root"] == "/music"
    assert body["state"] == "pending"
    assert body["job_id"] is not None


def test_get_import_session(client: TestClient, migrated_db: Path) -> None:
    start_resp = client.post("/api/imports", json={"library_root": "/music"})
    session_id = start_resp.json()["id"]

    resp = client.get(f"/api/imports/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert [t["stage"] for t in body["tasks"]] == ["scan", "fingerprint", "group", "match"]
    assert body["changeset_ids"] == []


def test_get_import_session_missing_404(client: TestClient) -> None:
    resp = client.get("/api/imports/99999")
    assert resp.status_code == 404


def test_resume_import(client: TestClient, migrated_db: Path) -> None:
    start_resp = client.post("/api/imports", json={"library_root": "/music"})
    session_id = start_resp.json()["id"]
    original_job_id = start_resp.json()["job_id"]

    resp = client.post(f"/api/imports/{session_id}/resume")
    assert resp.status_code == 200
    assert resp.json()["job_id"] != original_job_id


def test_resume_import_missing_404(client: TestClient) -> None:
    resp = client.post("/api/imports/99999/resume")
    assert resp.status_code == 404


def test_get_import_session_lists_changesets(client: TestClient, migrated_db: Path) -> None:
    from muzilla.db.models import ChangeSet

    start_resp = client.post("/api/imports", json={"library_root": "/music"})
    session_id = start_resp.json()["id"]

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        cs = ChangeSet(
            title="test", source="match_proposal", scope_type="group",
            import_session_id=session_id,
        )
        session.add(cs)
        session.commit()
        cs_id = cs.id

    resp = client.get(f"/api/imports/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["changeset_ids"] == [cs_id]
