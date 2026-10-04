from __future__ import annotations

import asyncio
import multiprocessing
import shutil
import threading
import time
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session, sessionmaker

from muzilla.config.schema import Config, JobsConfig, RetentionConfig
from muzilla.db.models import Job
from muzilla.domain.metadata import TrackMeta
from muzilla.jobs import queue, worker
from muzilla.jobs.handlers.enrich_lyrics import (
    handle_enrich_lyrics as _lyrics_handler,  # noqa: F401
)
from muzilla.jobs.handlers.scan import handle_scan as _scan_handler  # noqa: F401
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.sync import run_with_session
from muzilla.providers.set import ProviderSet

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _run_recovery_restart_process(database_path: str, started: Any, stop: Any) -> None:
    from muzilla.db.engine import create_db_engine, create_session_factory

    engine = create_db_engine(Path(database_path))
    factory = create_session_factory(engine)

    async def supervise() -> None:
        stop_event = asyncio.Event()
        recovery_task = asyncio.create_task(
            worker.run_lease_recovery_loop(factory, stop_event=stop_event, interval_seconds=0.05)
        )
        await asyncio.sleep(0.05)
        started.set()
        await asyncio.to_thread(stop.wait, 10)
        stop_event.set()
        await recovery_task

    asyncio.run(supervise())
    engine.dispose()


@pytest.fixture
def session_factory(db_session: Session) -> sessionmaker[Session]:
    """Tests need a real sessionmaker (worker.run_one opens/closes its
    own sessions per unit of work), bound to the same in-memory-backed
    file the db_session fixture already migrated."""
    bind = db_session.get_bind()
    return sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)


@pytest.fixture
def context() -> WorkerContext:
    """None of these tests' handlers use provider access — an empty
    ProviderSet is enough to satisfy the WorkerContext contract."""
    return WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=Config(),
    )


def _config(**overrides: object) -> JobsConfig:
    base = JobsConfig(job_timeout_seconds=5, lease_seconds=60, poll_interval_seconds=0.01)
    return base.model_copy(update=overrides)


async def test_run_one_succeeds(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    @register("test_worker_success")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        progress.update(1, total=1)
        return {"done": True}

    job = queue.enqueue(db_session, type="test_worker_success", payload={})

    ran = await worker.run_one(session_factory, worker_id="w1", config=_config(), context=context)
    assert ran is True

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "succeeded"
    assert refreshed.result == {"done": True}


def test_competing_workers_run_one_handler_once(
    db_session: Session,
    session_factory: sessionmaker[Session],
    context: WorkerContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from muzilla.jobs.execution_lock import JobExecutionLock

    selected = threading.Barrier(2)
    original_try_lock = JobExecutionLock.try_for_session_factory

    def synchronize_claims(
        cls: type[JobExecutionLock], factory: sessionmaker[Session], job_id: int
    ) -> JobExecutionLock | None:
        selected.wait(timeout=5)
        return original_try_lock(factory, job_id)

    monkeypatch.setattr(
        JobExecutionLock, "try_for_session_factory", classmethod(synchronize_claims)
    )
    started = threading.Event()
    release = threading.Event()
    invocation_count = 0
    invocation_lock = threading.Lock()

    @register("test_competing_worker_handler_once")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        nonlocal invocation_count
        with invocation_lock:
            invocation_count += 1
        started.set()
        assert await asyncio.to_thread(release.wait, 5)
        return {"handled": True}

    job = queue.enqueue(db_session, type="test_competing_worker_handler_once", payload={})

    def run(worker_id: str) -> bool:
        return asyncio.run(
            worker.run_one(session_factory, worker_id=worker_id, config=_config(), context=context)
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, f"worker-{index}") for index in range(2)]
        assert started.wait(timeout=5)
        release.set()
        results = [future.result(timeout=10) for future in futures]

    assert results.count(True) == 1
    assert results.count(False) == 1
    assert invocation_count == 1
    db_session.expire_all()
    completed = db_session.get(Job, job.id)
    assert completed is not None
    assert completed.state == "succeeded"
    assert completed.attempts == 1


async def test_run_one_returns_false_when_nothing_pending(
    session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    ran = await worker.run_one(session_factory, worker_id="w1", config=_config(), context=context)
    assert ran is False


async def test_periodic_recovery_reconciles_lease_after_it_expires(
    db_session: Session, session_factory: sessionmaker[Session]
) -> None:
    job = queue.enqueue(db_session, type="scan", payload={})
    leased = queue.lease_next(db_session, worker_id="old-worker", lease_seconds=60)
    assert leased is not None
    stop_event = asyncio.Event()
    recovery_task = asyncio.create_task(
        worker.run_lease_recovery_loop(
            session_factory, stop_event=stop_event, interval_seconds=0.01
        )
    )
    await asyncio.sleep(0.03)

    with session_factory() as session:
        expired = session.get(Job, job.id)
        assert expired is not None
        expired.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    recovered_state: str | None = None
    for _ in range(100):
        with session_factory() as session:
            current = session.get(Job, job.id)
            recovered_state = current.state if current is not None else None
        if recovered_state == "pending":
            break
        await asyncio.sleep(0.01)
    stop_event.set()
    await recovery_task
    assert recovered_state == "pending"


def test_restarted_process_recovers_lease_after_expiry_without_second_restart(
    db_session: Session, session_factory: sessionmaker[Session], migrated_db: Path
) -> None:
    job = queue.enqueue(db_session, type="scan", payload={})
    leased = queue.lease_next(db_session, worker_id="pre-restart-worker", lease_seconds=600)
    assert leased is not None and leased.id == job.id

    process_context = multiprocessing.get_context("spawn")
    started = process_context.Event()
    stop = process_context.Event()
    process = process_context.Process(
        target=_run_recovery_restart_process,
        args=(str(migrated_db), started, stop),
    )
    process.start()
    try:
        assert started.wait(timeout=5)
        time.sleep(0.1)
        with session_factory() as session:
            before_expiry = session.get(Job, job.id)
            assert before_expiry is not None and before_expiry.state == "running"
            assert before_expiry.attempts == 1
            before_expiry.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()

        recovered_state: str | None = None
        for _ in range(100):
            with session_factory() as session:
                current = session.get(Job, job.id)
                recovered_state = current.state if current is not None else None
            if recovered_state == "pending":
                break
            time.sleep(0.02)
        assert recovered_state == "pending"
        time.sleep(0.15)
        with session_factory() as session:
            recovered = session.get(Job, job.id)
            assert recovered is not None and recovered.state == "pending"
            assert recovered.attempts == 1
            state_events = [
                event.payload["state"]
                for event in queue.list_events_after(session, job.id, after_seq=0)
                if event.kind == "state"
            ]
            assert state_events == ["pending"]
    finally:
        stop.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert not process.is_alive()
    assert process.exitcode == 0


async def test_raising_handler_marks_failed_without_propagating(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    @register("test_worker_raises")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        raise ValueError("boom")

    job = queue.enqueue(db_session, type="test_worker_raises", payload={})

    ran = await worker.run_one(session_factory, worker_id="w1", config=_config(), context=context)
    assert ran is True

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "failed"
    assert refreshed.error is not None
    assert "boom" in refreshed.error


async def test_immediately_cancelled_pending_job_is_not_leased(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    @register("test_worker_cancelled_pending")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        for i in range(5):
            session.refresh(job)
            if job.cancel_requested:
                raise worker.JobCancelled
            progress.update(i, total=5)
        return {"done": True}

    job = queue.enqueue(db_session, type="test_worker_cancelled_pending", payload={})
    queue.request_cancel(db_session, job.id)

    ran = await worker.run_one(session_factory, worker_id="w1", config=_config(), context=context)
    assert ran is False

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "cancelled"


async def test_cancel_after_handler_result_keeps_partial_outcome(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    """The supervisor's final cancel read must not discard completed work."""

    started = asyncio.Event()
    release = asyncio.Event()

    @register("test_worker_cancel_after_result")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        started.set()
        await release.wait()
        return {"completed_items": 1}

    job = queue.enqueue(db_session, type="test_worker_cancel_after_result", payload={})
    run_task = asyncio.create_task(
        worker.run_one(session_factory, worker_id="w1", config=_config(), context=context)
    )
    await started.wait()

    with session_factory() as cancel_session:
        queue.request_cancel(cancel_session, job.id)
    release.set()
    assert await run_task is True

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "cancelled"
    assert refreshed.result == {"completed_items": 1, "partial": True}


async def test_real_scan_handler_observes_persisted_cancel_and_keeps_completed_index_rows(
    db_session: Session,
    session_factory: sessionmaker[Session],
    context: WorkerContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler gets a leased ORM row before cancellation is requested.

    This exercises the production worker + scan handler rather than a test
    handler that manually refreshes ``job``.  Cancellation arrives while the
    first real tag read is blocked; once released, the file is indexed, the
    next file boundary reads persisted cancellation state, and the job cannot
    report succeeded.
    """

    from muzilla.pipeline import scan as scan_pipeline

    library = tmp_path / "library"
    library.mkdir()
    shutil.copy(FIXTURES / "silence.mp3", library / "silence.mp3")
    started = threading.Event()
    release = threading.Event()
    real_read_track = scan_pipeline.read_track  # type: ignore[attr-defined]

    def blocking_read_track(path: Path) -> TrackMeta:
        started.set()
        assert release.wait(timeout=5)
        return real_read_track(path)

    monkeypatch.setattr(scan_pipeline, "read_track", blocking_read_track)
    job = queue.enqueue(db_session, type="scan", payload={"root": str(library)})

    run_task = asyncio.create_task(
        worker.run_one(session_factory, worker_id="w1", config=_config(), context=context)
    )
    assert await asyncio.to_thread(started.wait, 5)

    with session_factory() as cancel_session:
        queue.request_cancel(cancel_session, job.id)
    with session_factory() as observed_session:
        observed = observed_session.get(Job, job.id)
        assert observed is not None
        assert observed.state == "cancelling"

    await asyncio.sleep(0.06)  # exceed the token's bounded poll interval
    release.set()
    assert await run_task is True

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "cancelled"
    assert refreshed.result is not None
    assert refreshed.result["partial"] is True

    from muzilla.db.models import Track

    assert db_session.query(Track).count() == 1


async def test_real_lyrics_handler_discards_inflight_proposal_after_persisted_cancel(
    db_session: Session,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Cancellation after a provider response must not publish a review operation."""

    from muzilla.db.models import TaskAttempt, Track
    from muzilla.domain.metadata import LyricsResult
    from muzilla.pipeline.proposals import ProposalComposer
    from muzilla.pipeline.reviews import get_review_bundle, plan_task_attempt
    from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate

    track = Track(
        path=str(tmp_path / "song.flac"),
        filename="song.flac",
        ext=".flac",
        size_bytes=1,
        mtime_ns=1,
        title="Song",
        artist="Artist",
    )
    db_session.add(track)
    db_session.flush()
    review = ProposalComposer(db_session).compose_candidate_for_scope(
        scope_type="track",
        scope_id=track.id,
        candidate=ReleaseCandidate(
            source="musicbrainz",
            ref=ProviderRef(provider="musicbrainz", id="release-lyrics-cancel"),
            album="Album",
            album_artist="Artist",
            tracks=(CandidateTrack(position=1, title="Proposed song", artist="Artist"),),
        ),
    )
    before = get_review_bundle(db_session, review.id)
    assert before is not None
    before_revision_id = before.current_revision.id
    before_operation_ids = tuple(operation.id for operation in before.current_revision.operations)
    started = threading.Event()
    release = threading.Event()

    class BlockingLyricsProvider:
        async def get_lyrics(
            self, artist: str, title: str, duration_ms: int | None
        ) -> LyricsResult:
            started.set()
            assert await asyncio.to_thread(release.wait, 5)
            return LyricsResult(text="lyrics", synced=False, source="lrclib")

    context = WorkerContext(
        provider_set=ProviderSet(
            metadata={},
            art={},
            lyrics={"lrclib": cast(Any, BlockingLyricsProvider())},
            fingerprint={},
            clients=(),
        ),
        config=Config(),
    )
    job = queue.enqueue(
        db_session,
        type="enrich_lyrics",
        payload={"review_bundle_id": review.id},
        commit=False,
    )
    planned = plan_task_attempt(
        db_session,
        review.id,
        kind="lyrics",
        item_key=f"track:{track.id}",
        job_id=job.id,
    )
    db_session.commit()
    run_task = asyncio.create_task(
        worker.run_one(session_factory, worker_id="w1", config=_config(), context=context)
    )
    assert await asyncio.to_thread(started.wait, 5)
    with session_factory() as cancel_session:
        queue.request_cancel(cancel_session, job.id)
    await asyncio.sleep(0.06)
    release.set()
    assert await run_task is True

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "cancelled"
    attempt = db_session.get(TaskAttempt, planned.id)
    assert attempt is not None
    assert attempt.review_bundle_id == review.id
    assert attempt.state == "cancelled"
    after = get_review_bundle(db_session, review.id)
    assert after is not None
    assert after.id == review.id
    assert after.current_revision.id == before_revision_id
    assert (
        tuple(operation.id for operation in after.current_revision.operations)
        == before_operation_ids
    )


async def test_unknown_job_type_marks_failed(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    job = queue.enqueue(db_session, type="not_a_registered_type", payload={})

    ran = await worker.run_one(session_factory, worker_id="w1", config=_config(), context=context)
    assert ran is True

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "failed"
    assert refreshed.error is not None


async def test_slow_handler_times_out(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    @register("test_worker_slow")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        await asyncio.sleep(10)
        return {"done": True}

    job = queue.enqueue(db_session, type="test_worker_slow", payload={})

    ran = await worker.run_one(
        session_factory,
        worker_id="w1",
        config=_config(job_timeout_seconds=0.05),
        context=context,
    )
    assert ran is True

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "failed"
    assert refreshed.error is not None
    assert "timed out" in refreshed.error


async def test_supervised_sync_work_joins_after_repeated_cancellation() -> None:
    import threading

    from muzilla.jobs.sync import run_sync

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_work() -> str:
        started.set()
        assert release.wait(timeout=5)
        finished.set()
        return "joined-result"

    task = asyncio.create_task(run_sync(blocking_work))
    assert await asyncio.to_thread(started.wait, 5)
    for _ in range(3):
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert not finished.is_set()

    release.set()
    assert await task == "joined-result"
    assert finished.is_set()


async def test_durable_apply_result_wins_timeout_after_thread_join(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    started = threading.Event()
    release = threading.Event()
    durable_result = {"apply_run_id": 17, "state": "applied", "files": []}

    def committed_work() -> dict[str, object]:
        started.set()
        assert release.wait(timeout=5)
        return durable_result

    @register("test_worker_timeout_after_apply_commit")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        from muzilla.jobs.sync import run_sync

        return await run_sync(committed_work)

    job = queue.enqueue(db_session, type="test_worker_timeout_after_apply_commit", payload={})
    run_task = asyncio.create_task(
        worker.run_one(
            session_factory,
            worker_id="w1",
            config=_config(job_timeout_seconds=0.05),
            context=context,
        )
    )
    assert await asyncio.to_thread(started.wait, 5)
    await asyncio.sleep(0.1)
    db_session.expire_all()
    active = db_session.get(Job, job.id)
    assert active is not None and active.state == "cancelling"
    assert not run_task.done()

    release.set()
    assert await run_task is True
    db_session.expire_all()
    completed = db_session.get(Job, job.id)
    assert completed is not None and completed.state == "succeeded"
    assert completed.result == durable_result
    assert not completed.cancel_requested
    events = queue.list_events_after(db_session, job.id, after_seq=0)
    states = [event.payload["state"] for event in events if event.kind == "state"]
    assert states[-2:] == ["cancelling", "succeeded"]


async def test_timeout_joins_thread_owned_session_before_terminal_state(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    started = threading.Event()
    release = threading.Event()
    thread_session: Session | None = None

    def blocking_db_work(owned_session: Session) -> dict[str, object]:
        nonlocal thread_session
        thread_session = owned_session
        started.set()
        assert release.wait(timeout=5)
        assert owned_session is not db_session
        assert owned_session.get(Job, job.id) is not None
        return {"done": True}

    @register("test_worker_timeout_joins_thread")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        return await run_with_session(ctx.session_factory, session, blocking_db_work)

    job = queue.enqueue(db_session, type="test_worker_timeout_joins_thread", payload={})
    run_task = asyncio.create_task(
        worker.run_one(
            session_factory,
            worker_id="w1",
            config=_config(job_timeout_seconds=0.05),
            context=context,
        )
    )
    assert await asyncio.to_thread(started.wait, 5)
    await asyncio.sleep(0.1)

    db_session.expire_all()
    active = db_session.get(Job, job.id)
    assert active is not None
    assert active.state == "cancelling"
    assert not run_task.done()

    release.set()
    assert await run_task is True
    db_session.expire_all()
    finished = db_session.get(Job, job.id)
    assert finished is not None
    assert finished.state == "failed"
    assert finished.error is not None and "timed out" in finished.error
    assert thread_session is not None


async def test_recovery_leaves_live_expired_worker_until_its_thread_joins(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocking_work() -> dict[str, object]:
        started.set()
        assert release.wait(timeout=5)
        return {"done": True}

    @register("test_worker_live_expired_lock")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        from muzilla.jobs.sync import run_sync

        return await run_sync(blocking_work)

    job = queue.enqueue(db_session, type="test_worker_live_expired_lock", payload={})
    run_task = asyncio.create_task(
        worker.run_one(session_factory, worker_id="w1", config=_config(), context=context)
    )
    assert await asyncio.to_thread(started.wait, 5)
    with session_factory() as session:
        current = session.get(Job, job.id)
        assert current is not None
        current.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    with session_factory() as session:
        assert queue.recover_stuck_jobs(session) == 0
        current = session.get(Job, job.id)
        assert current is not None and current.state == "running"
    assert not run_task.done()

    release.set()
    assert await run_task is True
    db_session.expire_all()
    completed = db_session.get(Job, job.id)
    assert completed is not None and completed.state == "succeeded"


async def test_quiesce_cancels_and_joins_active_sync_work(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocking_work() -> dict[str, object]:
        started.set()
        assert release.wait(timeout=5)
        return {"done": True}

    @register("test_worker_quiesce_joins_thread")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        from muzilla.jobs.sync import run_sync

        return await run_sync(blocking_work)

    job = queue.enqueue(db_session, type="test_worker_quiesce_joins_thread", payload={})
    stop_event = asyncio.Event()
    run_task = asyncio.create_task(
        worker.run_one(
            session_factory,
            worker_id="w1",
            config=_config(),
            context=context,
            stop_event=stop_event,
        )
    )
    assert await asyncio.to_thread(started.wait, 5)
    stop_event.set()
    await asyncio.sleep(0.05)

    db_session.expire_all()
    active = db_session.get(Job, job.id)
    assert active is not None and active.state == "cancelling"
    assert not run_task.done()

    release.set()
    assert await run_task is True
    db_session.expire_all()
    cancelled = db_session.get(Job, job.id)
    assert cancelled is not None and cancelled.state == "cancelled"


async def test_provider_lease_releases_only_after_supervised_thread_joins(
    db_session: Session, session_factory: sessionmaker[Session]
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    client_closed = asyncio.Event()

    class ClosingProviderClient:
        async def aclose(self) -> None:
            client_closed.set()

    provider_client = ClosingProviderClient()
    old_set = ProviderSet(
        metadata={},
        art={},
        lyrics={},
        fingerprint={},
        clients=(cast(Any, provider_client),),
    )
    new_set = ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=())
    from muzilla.providers.runtime import ProviderSetRuntime

    runtime = ProviderSetRuntime(old_set)
    context = WorkerContext(provider_set=old_set, config=Config(), provider_runtime=runtime)

    def blocking_work() -> dict[str, object]:
        started.set()
        assert release.wait(timeout=5)
        finished.set()
        return {"done": True}

    @register("test_worker_provider_lease_join")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        from muzilla.jobs.sync import run_sync

        return await run_sync(blocking_work)

    job = queue.enqueue(db_session, type="test_worker_provider_lease_join", payload={})
    run_task = asyncio.create_task(
        worker.run_one(
            session_factory,
            worker_id="w1",
            config=_config(lease_seconds=3),
            context=context,
        )
    )
    assert await asyncio.to_thread(started.wait, 5)
    with session_factory() as session:
        active = session.get(Job, job.id)
        assert active is not None and active.lease_until is not None
        initial_lease = active.lease_until

    await runtime.swap(new_set)
    run_task.cancel()
    await asyncio.sleep(0.03)
    run_task.cancel()
    await asyncio.sleep(1.1)
    assert not client_closed.is_set()
    assert not run_task.done()
    with session_factory() as session:
        active = session.get(Job, job.id)
        assert active is not None and active.state == "cancelling"
        assert active.lease_until is not None
        assert active.lease_until.replace(tzinfo=UTC) > initial_lease.replace(tzinfo=UTC)

    from muzilla.jobs.execution_lock import JobExecutionLock

    assert JobExecutionLock.try_for_session_factory(session_factory, job.id) is None
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert finished.is_set()
    assert client_closed.is_set()
    lock = JobExecutionLock.try_for_session_factory(session_factory, job.id)
    assert lock is not None
    lock.close()
    db_session.expire_all()
    cancelled = db_session.get(Job, job.id)
    assert cancelled is not None and cancelled.state == "cancelled"


async def test_run_one_binds_job_id_to_log_context_during_handler(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    """Every log record emitted while a job runs
    carries job_id, via jobs/worker.py's job_context — bound around the
    handler call in _execute, not threaded through the handler
    signature."""
    from muzilla.logging import _job_id_var

    seen_job_id_inside_handler: int | None = None

    @register("test_worker_logs_job_id")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        nonlocal seen_job_id_inside_handler
        seen_job_id_inside_handler = _job_id_var.get()
        return {"done": True}

    job = queue.enqueue(db_session, type="test_worker_logs_job_id", payload={})

    await worker.run_one(session_factory, worker_id="w1", config=_config(), context=context)

    assert seen_job_id_inside_handler == job.id
    assert _job_id_var.get() is None  # reset after the job finishes


async def test_run_retention_loop_enqueues_immediately_at_startup(
    db_session: Session, session_factory: sessionmaker[Session]
) -> None:
    context = WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=Config(retention=RetentionConfig(enabled=True, sweep_interval_hours=24)),
    )
    stop_event = asyncio.Event()
    stop_event.set()  # loop body runs exactly once, then exits on the next check

    await worker.run_retention_loop(session_factory, stop_event=stop_event, context=context)

    jobs = db_session.query(Job).filter_by(type="retention_sweep").all()
    assert len(jobs) == 1


async def test_run_retention_loop_noop_when_disabled(
    db_session: Session, session_factory: sessionmaker[Session]
) -> None:
    context = WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=Config(retention=RetentionConfig(enabled=False)),
    )
    stop_event = asyncio.Event()
    stop_event.set()

    await worker.run_retention_loop(session_factory, stop_event=stop_event, context=context)

    assert db_session.query(Job).filter_by(type="retention_sweep").count() == 0


async def test_run_retention_loop_repeats_on_interval(
    db_session: Session, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A short sweep_interval_hours must produce more than one enqueue
    before the loop is stopped -- proves the wait-then-repeat half of
    the loop, not just the startup enqueue the other test covers."""
    # Config validation now enforces ge=0.1 (6 minutes); use the minimum valid
    # interval and accelerate the loop's asyncio.wait_for at the boundary so the
    # test does not sleep for real minutes while preserving recurrence semantics.
    context = WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=Config(retention=RetentionConfig(enabled=True, sweep_interval_hours=0.1)),
    )
    stop_event = asyncio.Event()

    real_wait_for = asyncio.wait_for

    async def _fast_wait_for[T](awaitable: Awaitable[T], timeout: float | None = None) -> T:
        # Shrink the 360s real timeout to a short deterministic wait so the loop iterates quickly;
        # if stop_event is set within the short window, return normally (loop exits), else raise TimeoutError
        # to trigger the next enqueue cycle. Preserve TimeoutError semantics (Python 3.11+ asyncio raises TimeoutError).
        return await real_wait_for(awaitable, timeout=0.02)

    monkeypatch.setattr(asyncio, "wait_for", _fast_wait_for)

    async def _stop_soon() -> None:
        await asyncio.sleep(0.1)
        stop_event.set()

    await asyncio.gather(
        worker.run_retention_loop(session_factory, stop_event=stop_event, context=context),
        _stop_soon(),
    )

    count = db_session.query(Job).filter_by(type="retention_sweep").count()
    assert count >= 2
