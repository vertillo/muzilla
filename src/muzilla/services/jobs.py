"""Job service: the only way api/cli reach `muzilla.jobs` for
enqueueing, inspecting, or cancelling background work, and the only
way `api/app.py`/the CLI's worker entrypoint start the worker pool or
run startup crash recovery.

Returns plain dataclasses, never db.models rows — same boundary
discipline as services/catalog.py and services/changesets.py.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import Job

# Importing the handler modules registers them (each decorates its
# function with @register at import time) — this import exists purely
# for that side effect, so the registry is populated before any job
# can be leased. Required here (not just in jobs/worker.py) since
# services is the only legal entry point api/cli/app.py has into jobs.
from muzilla.jobs import queue
from muzilla.jobs.handlers import fingerprint as _fingerprint_handler  # noqa: F401
from muzilla.jobs.handlers import group as _group_handler  # noqa: F401
from muzilla.jobs.handlers import import_session as _import_session_handler  # noqa: F401
from muzilla.jobs.handlers import match as _match_handler  # noqa: F401
from muzilla.jobs.handlers import scan as _scan_handler  # noqa: F401
from muzilla.jobs.registry import WorkerContext
from muzilla.jobs.worker import run_one, start_worker_pool
from muzilla.providers.set import ProviderSet
from muzilla.services.db import get_session_factory

_TERMINAL_STATES = ("succeeded", "failed", "cancelled")


@dataclass(frozen=True, slots=True)
class JobSummary:
    id: int
    type: str
    state: str
    priority: int
    progress_current: int
    progress_total: int | None
    progress_message: str | None
    attempts: int
    error: str | None


@dataclass(frozen=True, slots=True)
class JobDetail(JobSummary):
    payload: dict[str, object]
    result: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class JobPage:
    items: tuple[JobSummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class JobEventOut:
    seq: int
    kind: str
    payload: dict[str, object]


def _to_summary(job: Job) -> JobSummary:
    return JobSummary(
        id=job.id,
        type=job.type,
        state=job.state,
        priority=job.priority,
        progress_current=job.progress_current,
        progress_total=job.progress_total,
        progress_message=job.progress_message,
        attempts=job.attempts,
        error=job.error,
    )


def _to_detail(job: Job) -> JobDetail:
    s = _to_summary(job)
    return JobDetail(
        id=s.id,
        type=s.type,
        state=s.state,
        priority=s.priority,
        progress_current=s.progress_current,
        progress_total=s.progress_total,
        progress_message=s.progress_message,
        attempts=s.attempts,
        error=s.error,
        payload=dict(job.payload),
        result=dict(job.result) if job.result is not None else None,
    )


def enqueue_scan(session: Session, root: str) -> JobSummary:
    job = queue.enqueue(session, type="scan", payload={"root": root})
    return _to_summary(job)


def get_job(session: Session, job_id: int) -> JobDetail | None:
    job = queue.get_job(session, job_id)
    return _to_detail(job) if job is not None else None


def list_jobs(
    session: Session, *, state: str | None = None, cursor: str | None = None, limit: int = 100
) -> JobPage:
    items, next_cursor = queue.list_jobs(session, state=state, cursor=cursor, limit=limit)
    return JobPage(items=tuple(_to_summary(j) for j in items), next_cursor=next_cursor)


def list_job_events(session: Session, job_id: int, after: int) -> list[JobEventOut]:
    events = queue.list_events_after(session, job_id, after)
    return [JobEventOut(seq=e.seq, kind=e.kind, payload=dict(e.payload)) for e in events]


def request_job_cancel(session: Session, job_id: int) -> JobDetail:
    job = queue.get_job(session, job_id)
    if job is None:
        raise ValueError(f"job {job_id} not found")
    queue.request_cancel(session, job_id)
    detail = get_job(session, job_id)
    assert detail is not None
    return detail


def recover_stuck_jobs(session: Session) -> int:
    """Startup-only: resets jobs left `running` with an expired lease —
    the trace of a worker that died before clean shutdown."""
    return queue.recover_stuck_jobs(session)


async def run_worker_pool(
    config: Config, provider_set: ProviderSet, stop_event: asyncio.Event
) -> None:
    """The only entry point api/app.py's lifespan and the CLI's `jobs
    worker` command use to start the worker pool — neither may import
    muzilla.jobs directly."""
    session_factory = get_session_factory(config)
    context = WorkerContext(provider_set=provider_set, config=config)
    await start_worker_pool(
        session_factory, config=config.jobs, stop_event=stop_event, context=context
    )


async def run_job_once(
    session: Session, config: Config, provider_set: ProviderSet, job_id: int
) -> JobDetail:
    """Runs jobs one at a time (via jobs.worker.run_one) until the
    given job reaches a terminal state, then returns its detail.

    Used by CLI commands (`muzilla changes apply/undo`) that want the
    current "enqueue, run it now, print the result" synchronous feel
    without a separate `muzilla jobs worker` process running —
    executes through the exact same job handler the API's worker pool
    uses, just driven inline for one job. Bounded by `_TERMINAL_STATES`
    on the *target* job; other unrelated pending jobs may also run in
    the process if leased first, same as the real worker pool would.
    """
    session_factory = get_session_factory(config)
    context = WorkerContext(provider_set=provider_set, config=config)
    worker_id = f"cli-{job_id}"
    while True:
        # run_one writes via its own short-lived session (a different
        # Session object than the caller's); with expire_on_commit=False
        # the caller's session never sees those writes without an
        # explicit expire, so its cached Job row would otherwise look
        # permanently 'pending' and this loop would never terminate.
        session.expire_all()
        job = queue.get_job(session, job_id)
        assert job is not None
        if job.state in _TERMINAL_STATES:
            detail = get_job(session, job_id)
            assert detail is not None
            return detail
        ran = await run_one(session_factory, worker_id=worker_id, config=config.jobs, context=context)
        if not ran:
            # Nothing leasable right now but our job isn't terminal either
            # (a lease race is the only realistic cause in a single-CLI-
            # process context) -- briefly yield and check again.
            await asyncio.sleep(0.05)


__all__ = [
    "JobDetail",
    "JobEventOut",
    "JobPage",
    "JobSummary",
    "enqueue_scan",
    "get_job",
    "list_job_events",
    "list_jobs",
    "recover_stuck_jobs",
    "request_job_cancel",
    "run_job_once",
    "run_worker_pool",
]
