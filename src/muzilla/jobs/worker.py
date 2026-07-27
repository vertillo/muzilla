"""The asyncio job worker: poll loop + per-job supervisor.

No background task existed anywhere in the codebase before this —
`api/app.py`'s lifespan only builds/tears down the provider set. This
introduces the first `asyncio.create_task` pattern, tied into that
same lifespan shape (see services/jobs.py::run_worker_pool, the only
legal entry point for api/cli per the layering contract).

Single-writer discipline: `run_one` opens its own short-lived session
per unit of work (via the caller's `sessionmaker`) rather than holding
one session for the worker's entire lifetime — mirrors
services/db.py's per-unit-of-work pattern, so a worker task's writes
interleave safely with API-request reads under SQLite WAL.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable

from sqlalchemy.orm import Session, sessionmaker

from muzilla.config.schema import JobsConfig
from muzilla.db.models import Job
from muzilla.jobs import queue
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import JobHandler, WorkerContext, get_handler
from muzilla.logging import job_context

_logger = logging.getLogger(__name__)


async def run_one(
    session_factory: sessionmaker[Session],
    *,
    worker_id: str,
    config: JobsConfig,
    context: WorkerContext,
) -> bool:
    """Leases at most one pending job and runs it to completion
    (succeeded/failed/cancelled). Returns False if nothing was pending
    — the caller decides whether to sleep.

    This is what tests call directly, once per iteration — no need to
    spin up the full poll loop with real sleeps.
    """
    with session_factory() as session:
        job = queue.lease_next(session, worker_id=worker_id, lease_seconds=config.lease_seconds)
        if job is None:
            return False
        job_id = job.id
        job_type = job.type

    try:
        handler = get_handler(job_type)
    except KeyError as exc:
        with session_factory() as session:
            queue.mark_failed(session, job_id, str(exc))
        return True

    await _execute(session_factory, job_id, handler, config, context)
    return True


class JobCancelled(Exception):
    """Raised by a handler that observed cancel_requested and chose to
    stop early — not a failure, a cooperative exit."""


async def _execute(
    session_factory: sessionmaker[Session],
    job_id: int,
    handler: JobHandler,
    config: JobsConfig,
    context: WorkerContext,
) -> None:
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(session_factory, job_id, lease_seconds=config.lease_seconds)
    )
    try:
        with session_factory() as session:
            job = session.get(Job, job_id)
            assert job is not None
            with job_context(job_id):
                _logger.info("job start", extra={"job_type": job.type})
            reporter = ProgressReporter(session, job_id, coalesce_ms=config.event_coalesce_ms)
            try:
                with job_context(job_id):
                    result: dict[str, object] = await asyncio.wait_for(
                        handler(session, job, reporter, context), timeout=config.job_timeout_seconds
                    )
            except TimeoutError:
                reporter.flush()
                queue.mark_failed(
                    session, job_id, f"job timed out after {config.job_timeout_seconds}s"
                )
                with job_context(job_id):
                    _logger.warning("job end", extra={"job_type": job.type, "outcome": "failed"})
                return
            except JobCancelled:
                reporter.flush()
                queue.mark_cancelled(session, job_id)
                with job_context(job_id):
                    _logger.info("job end", extra={"job_type": job.type, "outcome": "cancelled"})
                return
            except Exception as exc:  # a crashed handler must never kill the worker loop
                reporter.flush()
                queue.mark_failed(session, job_id, str(exc))
                with job_context(job_id):
                    _logger.warning(
                        "job end", extra={"job_type": job.type, "outcome": "failed"}, exc_info=exc
                    )
                return

            reporter.flush()
            queue.mark_succeeded(session, job_id, result)
            with job_context(job_id):
                _logger.info("job end", extra={"job_type": job.type, "outcome": "succeeded"})
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


async def _heartbeat_loop(
    session_factory: sessionmaker[Session], job_id: int, *, lease_seconds: int
) -> None:
    """Renews the job's lease periodically while a handler runs, so a
    long-running stage isn't reclaimed by startup crash recovery."""
    interval = max(lease_seconds / 3, 1)
    while True:
        await asyncio.sleep(interval)
        with session_factory() as session:
            queue.heartbeat(session, job_id, lease_seconds=lease_seconds)


async def run_forever(
    session_factory: sessionmaker[Session],
    *,
    worker_id: str,
    config: JobsConfig,
    stop_event: asyncio.Event,
    context: WorkerContext,
) -> None:
    while not stop_event.is_set():
        ran = await run_one(session_factory, worker_id=worker_id, config=config, context=context)
        if not ran:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=config.poll_interval_seconds)


async def run_retention_loop(
    session_factory: sessionmaker[Session],
    *,
    stop_event: asyncio.Event,
    context: WorkerContext,
) -> None:
    """Enqueues a `retention_sweep` job once immediately (docs/PLAN.md
    §11c: "on worker startup") and then every
    `retention.sweep_interval_hours` until stopped. Deliberately just an
    asyncio.sleep loop rather than a scheduler dependency — §11c is
    explicit that this is sufficient and does not justify adding one.
    A no-op entirely when `retention.enabled` is False."""
    retention_config = context.config.retention
    if not retention_config.enabled:
        return

    interval_seconds = retention_config.sweep_interval_hours * 3600
    while True:
        # do-while shape, deliberately: the startup sweep must run even
        # if stop_event is already set by the time this task gets
        # scheduled (a fast shutdown racing startup) — a plain
        # `while not stop_event.is_set()` guard would silently skip it,
        # breaking "runs once at startup" for exactly the shutdown-soon
        # case where catching up on retention matters least but the
        # guarantee should still hold.
        with session_factory() as session:
            queue.enqueue(session, type="retention_sweep", payload={})
        if stop_event.is_set():
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        if stop_event.is_set():
            return


async def start_worker_pool(
    session_factory: sessionmaker[Session],
    *,
    config: JobsConfig,
    stop_event: asyncio.Event,
    context: WorkerContext,
) -> None:
    """Spawns `config.worker_concurrency` independent run_forever loops
    sharing one stop_event, each with a distinct worker_id, plus one
    retention-sweep loop (docs/PLAN.md §11c). Bounded concurrency via N
    separate short-lease loops rather than one loop leasing N jobs at
    once, keeping cancellation semantics simple."""
    workers: list[Awaitable[None]] = [
        run_forever(
            session_factory,
            worker_id=f"worker-{i}-{uuid.uuid4().hex[:8]}",
            config=config,
            stop_event=stop_event,
            context=context,
        )
        for i in range(config.worker_concurrency)
    ]
    workers.append(run_retention_loop(session_factory, stop_event=stop_event, context=context))
    await asyncio.gather(*workers)
