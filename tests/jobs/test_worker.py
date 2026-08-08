from __future__ import annotations

import asyncio
import shutil
import threading
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from muzilla.config.schema import Config, JobsConfig
from muzilla.db.models import Job
from muzilla.jobs import queue, worker
from muzilla.jobs.handlers.enrich_lyrics import (
    handle_enrich_lyrics as _lyrics_handler,  # noqa: F401
)
from muzilla.jobs.handlers.scan import handle_scan as _scan_handler  # noqa: F401
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.providers.set import ProviderSet

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


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


async def test_run_one_returns_false_when_nothing_pending(
    session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    ran = await worker.run_one(session_factory, worker_id="w1", config=_config(), context=context)
    assert ran is False


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
    real_read_track = scan_pipeline.read_track

    def blocking_read_track(path: Path):
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
            lyrics={"lrclib": BlockingLyricsProvider()},
            fingerprint={},
            clients=(),
        ),  # type: ignore[arg-type]
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
    assert tuple(operation.id for operation in after.current_revision.operations) == before_operation_ids


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


async def test_run_one_binds_job_id_to_log_context_during_handler(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    """docs/PLAN.md §11d: every log record emitted while a job runs
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
        config=Config(retention={"enabled": True, "sweep_interval_hours": 24}),
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
        config=Config(retention={"enabled": False}),
    )
    stop_event = asyncio.Event()
    stop_event.set()

    await worker.run_retention_loop(session_factory, stop_event=stop_event, context=context)

    assert db_session.query(Job).filter_by(type="retention_sweep").count() == 0


async def test_run_retention_loop_repeats_on_interval(
    db_session: Session, session_factory: sessionmaker[Session]
) -> None:
    """A short sweep_interval_hours must produce more than one enqueue
    before the loop is stopped -- proves the wait-then-repeat half of
    the loop, not just the startup enqueue the other test covers."""
    tiny_interval_hours = 0.01 / 3600  # ~0.01s
    context = WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=Config(retention={"enabled": True, "sweep_interval_hours": tiny_interval_hours}),
    )
    stop_event = asyncio.Event()

    async def _stop_soon() -> None:
        await asyncio.sleep(0.1)
        stop_event.set()

    await asyncio.gather(
        worker.run_retention_loop(session_factory, stop_event=stop_event, context=context),
        _stop_soon(),
    )

    count = db_session.query(Job).filter_by(type="retention_sweep").count()
    assert count >= 2
