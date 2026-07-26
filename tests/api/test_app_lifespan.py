from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muzilla.api.app import create_app


def test_worker_pool_starts_and_stops_cleanly(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")

    with TestClient(create_app()) as client:
        worker_task = client.app.state.worker_task
        assert worker_task is not None
        assert not worker_task.done()

    # TestClient's context manager exit drives the lifespan's shutdown
    # path (stop_event.set() + await worker_task) synchronously.
    assert worker_task.done()
    assert worker_task.exception() is None
