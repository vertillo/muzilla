from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.jobs import queue


def _enqueue_scan(db_path: Path) -> int:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    with factory() as session:
        job = queue.enqueue(session, type="scan", payload={"root": "/does/not/exist"})
        return job.id


def _read_sse_lines(client: TestClient, url: str, *, max_lines: int) -> list[str]:
    lines: list[str] = []
    with client.stream("GET", url) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line:
                lines.append(line)
            if len(lines) >= max_lines:
                break
    return lines


def test_job_events_replays_and_terminates(client: TestClient, migrated_db: Path) -> None:
    job_id = _enqueue_scan(migrated_db)

    # The real worker pool (running in the background per the client
    # fixture's app lifespan) will pick this job up and run it to
    # completion quickly since scan_library on a nonexistent root is a
    # fast no-op. Read lines until we see the terminal "done" event.
    lines = _read_sse_lines(client, f"/api/jobs/{job_id}/events", max_lines=50)
    assert any(line.startswith("event: done") for line in lines)


def test_job_events_missing_job_emits_error(client: TestClient) -> None:
    lines = _read_sse_lines(client, "/api/jobs/99999/events", max_lines=5)
    assert any(line.startswith("event: error") for line in lines)
