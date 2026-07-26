from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.orm import Session, sessionmaker

from muzilla.config.schema import JobsConfig
from muzilla.db.models import Job
from muzilla.jobs import queue, worker
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.providers.set import ProviderSet


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
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=())
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


async def test_cancellation_observed_mid_handler(
    db_session: Session, session_factory: sessionmaker[Session], context: WorkerContext
) -> None:
    @register("test_worker_cancellable")
    async def handle(
        session: Session, job: Job, progress: ProgressReporter, ctx: WorkerContext
    ) -> dict[str, object]:
        for i in range(5):
            session.refresh(job)
            if job.cancel_requested:
                raise worker.JobCancelled
            progress.update(i, total=5)
        return {"done": True}

    job = queue.enqueue(db_session, type="test_worker_cancellable", payload={})
    queue.request_cancel(db_session, job.id)

    ran = await worker.run_one(session_factory, worker_id="w1", config=_config(), context=context)
    assert ran is True

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "cancelled"


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
