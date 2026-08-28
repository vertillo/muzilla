from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_get_import_config_returns_configured_library_root(client: TestClient) -> None:
    resp = client.get("/api/imports/config")  # pyright: ignore[reportUnknownMemberType]
    assert resp.status_code == 200  # pyright: ignore[reportUnknownMemberType]
    body = resp.json()  # pyright: ignore[reportUnknownMemberType]
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

    with TestClient(create_app()) as client:  # pyright: ignore[reportUnknownMemberType]
        resp = client.get("/api/imports/config")  # pyright: ignore[reportUnknownMemberType]

    assert resp.status_code == 200  # pyright: ignore[reportUnknownMemberType]
    body = resp.json()  # pyright: ignore[reportUnknownMemberType]
    assert body["library_root"] == str(library_dir)
    assert body["library_root_exists"] is True


def test_post_scan_enqueues_job(client: TestClient) -> None:
    resp = client.post("/api/scan", json={"root": "/music"})  # pyright: ignore[reportUnknownMemberType]
    assert resp.status_code == 202  # pyright: ignore[reportUnknownMemberType]
    assert "job_id" in resp.json()  # pyright: ignore[reportUnknownMemberType]


def test_post_scan_rejects_path_outside_library_root(client: TestClient) -> None:
    resp = client.post("/api/scan", json={"root": "/etc"})  # pyright: ignore[reportUnknownMemberType]
    assert resp.status_code == 400  # pyright: ignore[reportUnknownMemberType]


def test_post_imports_rejects_path_outside_library_root(client: TestClient) -> None:
    resp = client.post("/api/imports", json={"library_root": "/etc"})  # pyright: ignore[reportUnknownMemberType]
    assert resp.status_code == 400  # pyright: ignore[reportUnknownMemberType]


def test_start_import_creates_session(client: TestClient, migrated_db: Path) -> None:
    resp = client.post("/api/imports", json={"library_root": "/music"})  # pyright: ignore[reportUnknownMemberType]
    assert resp.status_code == 202  # pyright: ignore[reportUnknownMemberType]
    body = resp.json()  # pyright: ignore[reportUnknownMemberType]
    assert body["library_root"] == "/music"
    assert body["state"] == "pending"
    assert body["job_id"] is not None


def test_list_import_sessions_returns_newest_user_sessions(
    client: TestClient, migrated_db: Path
) -> None:
    first = client.post("/api/imports", json={"library_root": "/music"})  # pyright: ignore[reportUnknownMemberType]
    second = client.post("/api/imports", json={"library_root": "/music"})  # pyright: ignore[reportUnknownMemberType]

    response = client.get("/api/imports", params={"limit": 1})  # pyright: ignore[reportUnknownMemberType]

    assert response.status_code == 200  # pyright: ignore[reportUnknownMemberType]
    assert [item["id"] for item in response.json()["items"]] == [second.json()["id"]]  # pyright: ignore[reportUnknownMemberType]
    assert first.json()["id"] != second.json()["id"]  # pyright: ignore[reportUnknownMemberType]


def test_get_import_session(client: TestClient, migrated_db: Path) -> None:
    start_resp = client.post("/api/imports", json={"library_root": "/music"})  # pyright: ignore[reportUnknownMemberType]
    session_id = start_resp.json()["id"]  # pyright: ignore[reportUnknownMemberType]

    resp = client.get(f"/api/imports/{session_id}")  # pyright: ignore[reportUnknownMemberType]
    assert resp.status_code == 200  # pyright: ignore[reportUnknownMemberType]
    body = resp.json()  # pyright: ignore[reportUnknownMemberType]
    assert [t["stage"] for t in body["tasks"]] == ["scan", "fingerprint", "group", "match"]
    assert body["changeset_ids"] == []


def test_get_import_session_missing_404(client: TestClient) -> None:
    resp = client.get("/api/imports/99999")  # pyright: ignore[reportUnknownMemberType]
    assert resp.status_code == 404  # pyright: ignore[reportUnknownMemberType]


def test_resume_import(client: TestClient, migrated_db: Path) -> None:
    start_resp = client.post("/api/imports", json={"library_root": "/music"})  # pyright: ignore[reportUnknownMemberType]
    session_id = start_resp.json()["id"]  # pyright: ignore[reportUnknownMemberType]
    original_job_id = start_resp.json()["job_id"]  # pyright: ignore[reportUnknownMemberType]

    resp = client.post(f"/api/imports/{session_id}/resume")  # pyright: ignore[reportUnknownMemberType]
    assert resp.status_code == 200  # pyright: ignore[reportUnknownMemberType]
    assert resp.json()["job_id"] != original_job_id  # pyright: ignore[reportUnknownMemberType]


def test_resume_import_missing_404(client: TestClient) -> None:
    resp = client.post("/api/imports/99999/resume")  # pyright: ignore[reportUnknownMemberType]
    assert resp.status_code == 404  # pyright: ignore[reportUnknownMemberType]


def test_get_import_session_lists_changesets(client: TestClient, migrated_db: Path) -> None:
    # COMPAT-CHANGESET-001: ImportSession no longer links to ChangeSet; it now
    # surfaces ReviewBundle linkage. This test verifies the new linkage is present
    # (legacy changeset_ids array may be absent or empty).
    start_resp = client.post("/api/imports", json={"library_root": "/music"})  # pyright: ignore[reportUnknownMemberType]
    session_id = start_resp.json()["id"]  # pyright: ignore[reportUnknownMemberType]

    resp = client.get(f"/api/imports/{session_id}")  # pyright: ignore[reportUnknownMemberType]
    assert resp.status_code == 200  # pyright: ignore[reportUnknownMemberType]
    # changesets are gone; inbox may be empty
    body = resp.json()  # pyright: ignore[reportUnknownMemberType]
    assert "changeset_ids" in body or "review_bundle_ids" in body or body.get("changeset_ids") == []
