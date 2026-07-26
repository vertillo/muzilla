from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.jobs import queue


def _enqueue_scan(db_path: Path, root: str = "/music") -> int:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    with factory() as session:
        job = queue.enqueue(session, type="scan", payload={"root": root})
        return job.id


def test_list_jobs(client: TestClient, migrated_db: Path) -> None:
    job_id = _enqueue_scan(migrated_db)

    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    ids = [j["id"] for j in resp.json()["items"]]
    assert job_id in ids


def test_get_job(client: TestClient, migrated_db: Path) -> None:
    job_id = _enqueue_scan(migrated_db, root="/some/library")

    resp = client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "scan"
    assert body["payload"] == {"root": "/some/library"}


def test_get_job_missing_404(client: TestClient) -> None:
    resp = client.get("/api/jobs/99999")
    assert resp.status_code == 404


def test_cancel_job(client: TestClient, migrated_db: Path) -> None:
    job_id = _enqueue_scan(migrated_db)

    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        job = queue.get_job(session, job_id)
        assert job is not None
        assert job.cancel_requested is True


def test_cancel_job_missing_404(client: TestClient) -> None:
    resp = client.post("/api/jobs/99999/cancel")
    assert resp.status_code == 404
