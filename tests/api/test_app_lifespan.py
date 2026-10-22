from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from muzilla.api.app import create_app, lifespan
from muzilla.db.models import Job
from muzilla.jobs import queue
from muzilla.jobs.execution_lock import JobExecutionLock
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.sync import run_with_session
from muzilla.providers.set import ProviderSet
from muzilla.services.db import get_session_factory


def test_worker_pool_starts_and_stops_cleanly(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")

    app = create_app()
    with TestClient(app):
        worker_task = app.state.worker_task
        assert worker_task is not None
        assert not worker_task.done()

    # TestClient's context manager exit drives the lifespan's shutdown
    # path (stop_event.set() + await worker_task) synchronously.
    assert worker_task.done()
    assert worker_task.exception() is None


async def test_lifespan_shutdown_joins_blocked_mutation_before_releasing_resources(
    migrated_db: Path, tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    session_closed = threading.Event()
    client_closed = asyncio.Event()
    mutations: list[str] = []

    class ClosingClient:
        async def aclose(self) -> None:
            assert finished.is_set() and session_closed.is_set()
            client_closed.set()

    provider_set = ProviderSet(
        metadata={}, art={}, lyrics={}, fingerprint={}, clients=(cast(Any, ClosingClient()),)
    )
    monkeypatch.setattr(
        "muzilla.api.app.providers_service.build_provider_set", lambda _config: provider_set
    )
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("MUZILLA_STORAGE__BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(tmp_path / "library"))
    monkeypatch.setenv("MUZILLA_STORAGE__PROVIDER_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    monkeypatch.setenv("MUZILLA_PROVIDERS_OFFLINE", "true")
    monkeypatch.setenv("MUZILLA_JOBS__WORKER_CONCURRENCY", "1")
    monkeypatch.setenv("MUZILLA_JOBS__POLL_INTERVAL_SECONDS", "0.01")

    def blocking_mutation(owned_session: Session) -> dict[str, object]:
        assert owned_session is not db_session
        owner_thread = threading.get_ident()
        original_close = owned_session.close

        def close_owned_session() -> None:
            assert threading.get_ident() == owner_thread
            assert finished.is_set()
            original_close()
            session_closed.set()

        monkeypatch.setattr(owned_session, "close", close_owned_session)
        started.set()
        assert release.wait(timeout=10)
        queue.append_event(owned_session, job_id, "shutdown_effect", {"committed": True})
        owned_session.commit()
        mutations.append("committed")
        finished.set()
        return {"done": True}

    @register("test_lifespan_shutdown_blocked_mutation")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        return await run_with_session(ctx.session_factory, session, blocking_mutation)

    job = queue.enqueue(db_session, type="test_lifespan_shutdown_blocked_mutation", payload={})
    db_session.commit()
    job_id = job.id
    app = create_app()
    manager = lifespan(app)
    await manager.__aenter__()
    shutdown: asyncio.Task[bool | None] | None = None
    try:
        assert await asyncio.to_thread(started.wait, 5)
        shutdown = asyncio.create_task(manager.__aexit__(None, None, None))
        async with asyncio.timeout(5):
            while True:
                db_session.expire_all()
                active = db_session.get(Job, job.id)
                assert active is not None
                if active.state == "cancelling":
                    break
                await asyncio.sleep(0.01)
        assert not shutdown.done()
        assert not app.state.worker_task.done()
        assert not finished.is_set()
        assert not session_closed.is_set()
        assert not client_closed.is_set()
        assert mutations == []
        factory = get_session_factory(app.state.config)
        assert JobExecutionLock.try_for_session_factory(factory, job.id) is None
    finally:
        release.set()
        if shutdown is None:
            await manager.__aexit__(None, None, None)
        else:
            await asyncio.wait_for(shutdown, timeout=10)

    assert app.state.worker_task.done()
    assert finished.is_set() and session_closed.is_set() and client_closed.is_set()
    db_session.expire_all()
    terminal = db_session.get(Job, job.id)
    assert terminal is not None and terminal.state == "cancelled"
    assert mutations == ["committed"]
    events = queue.list_events_after(db_session, job.id, after_seq=0)
    assert sum(event.kind == "shutdown_effect" for event in events) == 1
    lock = JobExecutionLock.try_for_session_factory(factory, job.id)
    assert lock is not None
    lock.close()
    await asyncio.sleep(0.05)
    assert mutations == ["committed"]
