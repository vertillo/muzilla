"""Supervise the synchronous *request* stage, before any Apply worker runs."""

from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from datetime import UTC
from pathlib import Path
from threading import Event, Timer, get_ident
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.api.app import create_app
from muzilla.db.models import ApplyRun, Job, Operation, ReviewBundle, Track
from muzilla.domain.metadata import tag_hash
from muzilla.domain.reviews import BundleState
from muzilla.jobs import queue
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.pipeline.reviews import (
    OperationDraft,
    put_revision,
    transition_bundle,
)
from muzilla.services import reset as reset_service
from muzilla.services import review_apply, reviews
from muzilla.services.db import get_session_factory
from muzilla.tags import reader
from muzilla.tags.reader import read_track


@pytest.mark.parametrize("operation", ["apply", "refresh"])
@pytest.mark.parametrize("cancel_request", [False, True])
async def test_request_io_is_responsive_and_joined_before_reset(
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    cancel_request: bool,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    for key, value in {
        "STORAGE__DB_PATH": migrated_db,
        "STORAGE__LIBRARY_ROOT": library,
        "STORAGE__DATA_DIR": tmp_path / "data",
        "STORAGE__CACHE_DIR": tmp_path / "cache",
        "STORAGE__BLOB_DIR": tmp_path / "blobs",
        "STORAGE__PROVIDER_SECRETS_DIR": tmp_path / "secrets/providers",
        "AUTH__ENABLED": "false",
        "PROVIDERS_OFFLINE": "true",
        "JOBS__POLL_INTERVAL_SECONDS": "0.01",
        "JOBS__LEASE_SECONDS": "3",
        "RETENTION__ENABLED": "false",
    }.items():
        monkeypatch.setenv(f"MUZILLA_{key}", str(value))

    entered, release, finished = Event(), Event(), Event()
    worker_started = asyncio.Event()
    worker_release = asyncio.Event()
    endpoint_tasks: list[asyncio.Task[Any]] = []
    session_threads: dict[int, int] = {}
    owned_sessions: list[Session] = []
    closed: list[int] = []
    main_thread = get_ident()
    original_init, original_close = Session.__init__, Session.close

    def record_init(session: Session, *args: Any, **kwargs: Any) -> None:
        original_init(session, *args, **kwargs)
        session_threads[id(session)] = get_ident()

    def record_close(session: Session) -> None:
        if session in owned_sessions:
            assert finished.is_set(), "session closed before synchronous work finished"
            assert session_threads[id(session)] == get_ident() != main_thread
            closed.append(id(session))
        original_close(session)

    monkeypatch.setattr(Session, "__init__", record_init)
    monkeypatch.setattr(Session, "close", record_close)

    @register("test_http_supervision_heartbeat")
    async def heartbeat_job(
        session: Session,
        job: Job,
        progress: ProgressReporter,
        context: WorkerContext,
    ) -> dict[str, object]:
        worker_started.set()
        await worker_release.wait()
        return {"completed": True}

    app = create_app()
    service_module = review_apply if operation == "apply" else reviews
    async_name = (
        "enqueue_review_apply_async" if operation == "apply" else "refresh_review_bundle_async"
    )
    original_async_service = getattr(service_module, async_name)

    async def capture_endpoint(*args: Any, **kwargs: Any) -> Any:
        task = asyncio.current_task()
        assert task is not None
        endpoint_tasks.append(task)
        return await original_async_service(*args, **kwargs)

    monkeypatch.setattr(service_module, async_name, capture_endpoint)

    async with app.router.lifespan_context(app):
        factory = get_session_factory(app.state.config)
        path = library / "source.mp3"
        shutil.copy(Path(__file__).parents[1] / "fixtures/audio/silence.mp3", path)
        meta, stat = read_track(path), path.stat()
        with factory() as session:
            track = Track(
                path=str(path),
                filename=path.name,
                ext=".mp3",
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                tag_hash=tag_hash(meta),
                title=meta.title,
            )
            session.add(track)
            session.flush()
            session.commit()
            revision = put_revision(
                session,
                logical_key=f"track:{track.id}",
                title="Request supervision",
                scope_type="track",
                scope_id=track.id,
                source_snapshot={
                    "items": [
                        {
                            "source_type": "track",
                            "source_id": track.id,
                            "path": str(path),
                            "filename": path.name,
                            "size_bytes": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns,
                            "tag_hash": track.tag_hash,
                        }
                    ]
                },
                operations=(
                    OperationDraft(
                        kind="set_tag",
                        field="title",
                        target_type="track",
                        target_id=track.id,
                        current_value=track.title,
                        proposed_value="Reviewed",
                    ),
                ),
            )
            op = session.scalar(
                select(Operation).where(Operation.proposal_revision_id == revision.revision_id)
            )
            assert op is not None
            op.decision = "accepted"
            transition_bundle(session, revision.bundle_id, BundleState.READY)
            session.commit()
            bundle_id, old_revision_id = revision.bundle_id, revision.revision_id
            job = queue.enqueue(session, type="test_http_supervision_heartbeat", payload={})
            job_id = job.id
        if operation == "refresh":
            from muzilla.tags.writer import write_fields

            write_fields(path, {"title": "Externally changed before Refresh"})
        await asyncio.wait_for(worker_started.wait(), 3)

        original_service = (
            review_apply.enqueue_review_apply
            if operation == "apply"
            else reviews.refresh_review_bundle
        )

        def own_session(session: Session, *args: Any, **kwargs: Any) -> Any:
            owned_sessions.append(session)
            try:
                return original_service(session, *args, **kwargs)
            finally:
                finished.set()

        monkeypatch.setattr(
            review_apply if operation == "apply" else reviews,
            "enqueue_review_apply" if operation == "apply" else "refresh_review_bundle",
            own_session,
        )
        original_io = (
            review_apply._preflight_for_apply if operation == "apply" else reader.read_track
        )

        def block_io(*args: Any, **kwargs: Any) -> Any:
            if not entered.is_set():
                entered.set()
                assert release.wait(8), "request barrier timed out"
            return original_io(*args, **kwargs)

        monkeypatch.setattr(
            review_apply if operation == "apply" else reader,
            "_preflight_for_apply" if operation == "apply" else "read_track",
            block_io,
        )
        reset_entered = Event()
        original_reset = reset_service.execute_prepared_reset

        def record_reset(*args: Any, **kwargs: Any) -> Any:
            assert finished.is_set() and closed
            # A cancelled HTTP waiter does not erase a durable enqueue/promotion.
            with factory() as session:
                if operation == "apply":
                    assert (
                        session.scalar(
                            select(ApplyRun).where(ApplyRun.review_bundle_id == bundle_id)
                        )
                        is not None
                    )
                else:
                    detail = reviews.get_review_bundle(session, bundle_id)
                    assert detail is not None and detail.current_revision is not None
                    assert detail.current_revision.id != old_revision_id
            reset_entered.set()
            return original_reset(*args, **kwargs)

        monkeypatch.setattr(reset_service, "execute_prepared_reset", record_reset)
        watchdog = Timer(7, release.set)
        watchdog.start()
        request_task = reset_task = None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                token = (await client.get("/api/auth/status")).json()["csrf_token"]
                client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": token})
                request_task = asyncio.create_task(
                    client.post(
                        f"/api/reviews/{bundle_id}/{operation}",
                        headers={"Idempotency-Key": "request-stage"},
                    )
                )
                assert await asyncio.to_thread(entered.wait, 3)
                assert not release.is_set(), (
                    "event loop stalled until watchdog released request I/O"
                )
                with factory() as session:
                    active_job = session.get(Job, job_id)
                    assert active_job is not None and active_job.lease_until is not None
                    initial_lease = active_job.lease_until
                assert (await asyncio.wait_for(client.get("/api/health"), 1)).status_code == 200
                assert (
                    await asyncio.wait_for(client.get(f"/api/reviews/{bundle_id}"), 1)
                ).status_code == 200
                await asyncio.sleep(1.1)
                with factory() as session:
                    active_job = session.get(Job, job_id)
                    assert active_job is not None and active_job.lease_until is not None
                    current_lease = active_job.lease_until
                    assert current_lease.replace(tzinfo=UTC) > initial_lease.replace(tzinfo=UTC)
                cancel = await asyncio.wait_for(client.post(f"/api/jobs/{job_id}/cancel"), 1)
                assert cancel.status_code == 200 and cancel.json()["state"] == "cancelling"
                assert not release.is_set()
                if cancel_request:
                    request_task.cancel()
                    # Cancel both the outer middleware waiter and the actual endpoint,
                    # repeatedly: neither cancellation is permission to abandon its thread.
                    for _ in range(3):
                        endpoint_tasks[0].cancel()
                        await asyncio.sleep(0.02)
                    assert not endpoint_tasks[0].done()
                    assert not finished.is_set() and not closed
                    reset_task = asyncio.create_task(
                        client.post(
                            "/api/settings/reset/catalog",
                            headers={"Idempotency-Key": "after-cancel"},
                            json={
                                "scope": "catalog_and_activity",
                                "confirmation": "RESET CATALOG AND ACTIVITY",
                            },
                        )
                    )
                    await asyncio.sleep(0.1)
                    assert not reset_task.done() and not reset_entered.is_set()
                    with factory() as session:
                        assert session.get(ReviewBundle, bundle_id) is not None
                release.set()
                worker_release.set()
                if cancel_request:
                    with suppress(asyncio.CancelledError):
                        await request_task
                    with suppress(asyncio.CancelledError):
                        await asyncio.wait_for(endpoint_tasks[0], 3)
                    assert reset_task is not None
                    reset = await asyncio.wait_for(reset_task, 5)
                    assert reset.status_code == 200, reset.text
                    assert reset_entered.is_set()
                else:
                    response = await asyncio.wait_for(request_task, 3)
                    assert response.status_code == (202 if operation == "apply" else 200), (
                        response.text
                    )
                    if operation == "apply":
                        repeated = await client.post(
                            f"/api/reviews/{bundle_id}/apply",
                            headers={"Idempotency-Key": "request-stage"},
                        )
                        assert repeated.status_code == 202 and repeated.json() == response.json()
                        with factory() as session:
                            assert (
                                session.scalar(
                                    select(ApplyRun).where(ApplyRun.review_bundle_id == bundle_id)
                                )
                                is not None
                            )
                    else:
                        assert response.json()["current_revision"]["id"] != old_revision_id
                        assert (
                            response.json()["current_revision"]["operations"][0]["proposed_value"]
                            == "Reviewed"
                        )
                assert finished.is_set() and closed
        finally:
            release.set()
            worker_release.set()
            watchdog.cancel()
            watchdog.join()
            for task in (request_task, reset_task, *endpoint_tasks):
                if task is not None:
                    with suppress(asyncio.CancelledError, Exception):
                        await asyncio.wait_for(task, 10)
    assert factory.kw["bind"].pool.checkedout() == 0


@pytest.mark.parametrize(
    "concurrent_action", ["heartbeat", "edit", "apply", "path", "decision", "promotion_failure"]
)
async def test_refresh_observation_releases_database_and_revalidates_promotion(
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    concurrent_action: str,
) -> None:
    from muzilla.db.models import ProposalRevision, SourceSnapshot
    from muzilla.pipeline.reviews import start_apply_run
    from muzilla.tags.writer import write_fields

    library = tmp_path / "library"
    library.mkdir()
    for key, value in {
        "STORAGE__DB_PATH": migrated_db,
        "STORAGE__LIBRARY_ROOT": library,
        "STORAGE__DATA_DIR": tmp_path / "data",
        "STORAGE__CACHE_DIR": tmp_path / "cache",
        "STORAGE__BLOB_DIR": tmp_path / "blobs",
        "STORAGE__PROVIDER_SECRETS_DIR": tmp_path / "secrets/providers",
        "AUTH__ENABLED": "false",
        "PROVIDERS_OFFLINE": "true",
        "JOBS__POLL_INTERVAL_SECONDS": "0.01",
        "JOBS__LEASE_SECONDS": "3",
        "RETENTION__ENABLED": "false",
    }.items():
        monkeypatch.setenv(f"MUZILLA_{key}", str(value))
    entered, release = Event(), Event()
    worker_started, worker_release = asyncio.Event(), asyncio.Event()

    @register("test_refresh_observation_heartbeat")
    async def heartbeat_job(
        session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
    ) -> dict[str, object]:
        worker_started.set()
        await worker_release.wait()
        return {"completed": True}

    app = create_app()
    async with app.router.lifespan_context(app):
        factory = get_session_factory(app.state.config)
        paths = [
            library / f"source-{index}.mp3"
            for index in range(2 if concurrent_action == "heartbeat" else 1)
        ]
        job_id: int | None = None
        original_facts: list[tuple[int, int, int, str | None]] = []
        items: list[dict[str, object]] = []
        with factory() as session:
            for path in paths:
                shutil.copy(Path(__file__).parents[1] / "fixtures/audio/silence.mp3", path)
                stat, meta = path.stat(), read_track(path)
                track = Track(
                    path=str(path),
                    filename=path.name,
                    ext=".mp3",
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    tag_hash=tag_hash(meta),
                    title=meta.title,
                )
                session.add(track)
                session.flush()
                original_facts.append((track.id, track.size_bytes, track.mtime_ns, track.tag_hash))
                items.append(
                    {
                        "source_type": "track",
                        "source_id": track.id,
                        "path": str(path),
                        "filename": path.name,
                        "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "tag_hash": track.tag_hash,
                    }
                )
            revision = put_revision(
                session,
                logical_key=f"track:{original_facts[0][0]}",
                title="Refresh race",
                scope_type="track",
                scope_id=original_facts[0][0],
                source_snapshot={"items": items},
                operations=(
                    OperationDraft(
                        kind="set_tag",
                        field="title",
                        target_type="track",
                        target_id=original_facts[0][0],
                        current_value=None,
                        proposed_value="Reviewed",
                    ),
                ),
            )
            op = session.scalar(
                select(Operation).where(Operation.proposal_revision_id == revision.revision_id)
            )
            assert op is not None
            op.decision = "accepted"
            operation_id = op.id
            transition_bundle(session, revision.bundle_id, BundleState.READY)
            session.commit()
            bundle_id, revision_id = revision.bundle_id, revision.revision_id
            if concurrent_action == "heartbeat":
                job_id = queue.enqueue(
                    session, type="test_refresh_observation_heartbeat", payload={}
                ).id
        if concurrent_action != "apply":
            write_fields(paths[0], {"title": "Externally changed"})
        if concurrent_action == "heartbeat":
            await asyncio.wait_for(worker_started.wait(), 3)
        original_read = reader.read_track

        def block_read(path: Path) -> Any:
            if path == paths[-1]:
                entered.set()
                assert release.wait(10), "Refresh observation barrier timed out"
            return original_read(path)

        monkeypatch.setattr(reader, "read_track", block_read)
        watchdog = Timer(9, release.set)
        watchdog.start()
        request_task = None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                token = (await client.get("/api/auth/status")).json()["csrf_token"]
                client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": token})
                request_task = asyncio.create_task(client.post(f"/api/reviews/{bundle_id}/refresh"))
                assert await asyncio.to_thread(entered.wait, 3)
                assert not release.is_set()
                if concurrent_action != "heartbeat":
                    assert factory.kw["bind"].pool.checkedout() == 0
                if concurrent_action == "heartbeat":
                    assert job_id is not None
                    assert (await asyncio.wait_for(client.get("/api/health"), 1)).status_code == 200
                    assert (
                        await asyncio.wait_for(client.get(f"/api/reviews/{bundle_id}"), 1)
                    ).status_code == 200
                    with factory() as session:
                        job = session.get(Job, job_id)
                        assert job is not None and job.lease_until is not None
                        initial_lease = job.lease_until
                    await asyncio.sleep(1.1)
                    with factory() as session:
                        job = session.get(Job, job_id)
                        assert job is not None and job.lease_until is not None
                        assert job.lease_until.replace(tzinfo=UTC) > initial_lease.replace(
                            tzinfo=UTC
                        )
                    cancel = await asyncio.wait_for(client.post(f"/api/jobs/{job_id}/cancel"), 1)
                    assert cancel.status_code == 200 and cancel.json()["state"] == "cancelling"
                elif concurrent_action == "edit":
                    edit = await asyncio.wait_for(
                        client.post(
                            f"/api/reviews/{bundle_id}/operations/{operation_id}/edit",
                            json={
                                "revision_id": revision_id,
                                "kind": "set_tag",
                                "value": "Concurrent edit",
                            },
                        ),
                        2,
                    )
                    assert edit.status_code == 200, edit.text
                    revision_id = edit.json()["current_revision"]["id"]
                elif concurrent_action == "promotion_failure":
                    from muzilla.pipeline import reviews as review_pipeline

                    def fail_promotion(*args: Any, **kwargs: Any) -> Any:
                        raise reviews.ReviewInvariantError("injected promotion failure")

                    monkeypatch.setattr(review_pipeline, "put_revision", fail_promotion)
                else:
                    with factory() as session:
                        if concurrent_action == "apply":
                            start_apply_run(session, bundle_id, idempotency_key="during-refresh")
                        elif concurrent_action == "path":
                            changed_track = session.get(Track, original_facts[0][0])
                            assert changed_track is not None
                            changed_track.path = str(library / "concurrently-moved.mp3")
                        else:
                            changed_operation = session.get(Operation, operation_id)
                            assert changed_operation is not None
                            changed_operation.decision = "rejected"
                        session.commit()
                assert not release.is_set()
                release.set()
                response = await asyncio.wait_for(request_task, 3)
                assert response.status_code == (
                    200 if concurrent_action in {"heartbeat", "decision"} else 409
                ), response.text
                if concurrent_action == "decision":
                    assert (
                        response.json()["current_revision"]["operations"][0]["decision"]
                        == "rejected"
                    )
                if concurrent_action in {"heartbeat", "decision"}:
                    repeated = await client.post(f"/api/reviews/{bundle_id}/refresh")
                    assert repeated.status_code == 200, repeated.text
                    assert (
                        repeated.json()["current_revision"] == response.json()["current_revision"]
                    )
                else:
                    with factory() as session:
                        detail = reviews.get_review_bundle(session, bundle_id)
                        assert detail is not None and detail.current_revision is not None
                        assert detail.current_revision.id == revision_id
                        if concurrent_action == "edit":
                            assert (
                                detail.current_revision.operations[0].proposed_value
                                == "Concurrent edit"
                            )
                        elif concurrent_action == "apply":
                            assert detail.state == "applying"
                            assert (
                                session.scalar(
                                    select(ApplyRun).where(ApplyRun.review_bundle_id == bundle_id)
                                )
                                is not None
                            )
                        assert len(list(session.scalars(select(ProposalRevision)))) == (
                            2 if concurrent_action == "edit" else 1
                        )
                        assert len(list(session.scalars(select(SourceSnapshot)))) == 1
                        for track_id, size, mtime, digest in original_facts:
                            stored_track = session.get(Track, track_id)
                            assert stored_track is not None
                            if concurrent_action == "path":
                                assert stored_track.path == str(library / "concurrently-moved.mp3")
                            assert (
                                stored_track.size_bytes,
                                stored_track.mtime_ns,
                                stored_track.tag_hash,
                            ) == (size, mtime, digest)
        finally:
            release.set()
            worker_release.set()
            watchdog.cancel()
            watchdog.join()
            if request_task is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(request_task, 12)
    assert factory.kw["bind"].pool.checkedout() == 0
