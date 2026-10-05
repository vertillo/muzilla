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
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker  # pyright: ignore[reportMissingImports]

from muzilla.config.schema import JobsConfig
from muzilla.db.models import Job
from muzilla.jobs import queue
from muzilla.jobs.cancellation import CancellationToken, bind_token
from muzilla.jobs.execution_lock import JobExecutionLock
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import JobHandler, WorkerContext, get_handler
from muzilla.jobs.sync import run_sync
from muzilla.logging import job_context

_logger = logging.getLogger(__name__)


async def run_one(
    session_factory: sessionmaker[Session],
    *,
    worker_id: str,
    config: JobsConfig,
    context: WorkerContext,
    stop_event: asyncio.Event | None = None,
) -> bool:
    """Leases at most one pending job and runs it to completion
    (succeeded/failed/cancelled). Returns False if nothing was pending
    — the caller decides whether to sleep.

    This is what tests call directly, once per iteration — no need to
    spin up the full poll loop with real sleeps.
    """
    with session_factory() as session:
        candidate_id = queue.next_pending_job_id(session)
        session.rollback()
    if candidate_id is None:
        return False
    execution_lock = JobExecutionLock.try_for_session_factory(session_factory, candidate_id)
    if execution_lock is None:
        return False
    try:
        with session_factory() as session:
            job = queue.lease_next(
                session,
                worker_id=worker_id,
                lease_seconds=config.lease_seconds,
                candidate_id=candidate_id,
            )
            if job is None:
                return False
            job_id = job.id
            job_type = job.type
            attempts = job.attempts

        try:
            handler = get_handler(job_type)
        except KeyError as exc:
            with session_factory() as session:
                queue.mark_failed(session, job_id, str(exc), worker_id=worker_id, attempts=attempts)
            return True

        await _execute(
            session_factory,
            job_id,
            worker_id,
            attempts,
            handler,
            config,
            context,
            stop_event=stop_event,
        )
        return True
    finally:
        execution_lock.close()


async def _run_supervised_sync[T](function: Callable[..., T], /, *args: object) -> tuple[T, bool]:
    task = asyncio.current_task()
    cancellations_before = task.cancelling() if task is not None else 0
    result: T = await run_sync(function, *args)
    interrupted = task is not None and task.cancelling() > cancellations_before
    return result, interrupted


def _request_cancel_in_session(session_factory: sessionmaker[Session], job_id: int) -> None:
    with session_factory() as session:
        queue.request_cancel(session, job_id)


def _heartbeat_in_session(
    session_factory: sessionmaker[Session],
    job_id: int,
    worker_id: str,
    attempts: int,
    lease_seconds: int,
) -> bool:
    with session_factory() as session:
        return queue.heartbeat(
            session,
            job_id,
            worker_id=worker_id,
            attempts=attempts,
            lease_seconds=lease_seconds,
        )


class JobCancelled(Exception):
    """Raised by a handler that observed cancel_requested and chose to
    stop early — not a failure, a cooperative exit."""

    def __init__(self, result: dict[str, object] | None = None) -> None:
        super().__init__()
        self.result = result


async def _drain_task[T](task: asyncio.Task[T]) -> bool:
    """Join a child task despite repeated cancellation of its supervisor."""
    supervisor = asyncio.current_task()
    caller_cancelled = False
    while not task.done():
        cancellations_before = supervisor.cancelling() if supervisor is not None else 0
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if supervisor is not None and supervisor.cancelling() > cancellations_before:
                caller_cancelled = True
        except BaseException:
            # A completed child may have failed; its owner retrieves the
            # exception after the join. Never let that skip resource cleanup.
            if not task.done():
                raise
    return caller_cancelled


def _durable_review_result(
    session_factory: sessionmaker[Session], job_type: str, payload: dict[str, object]
) -> dict[str, object] | None:
    """Rebuild a committed Apply/Undo outcome if its handler response was lost."""
    from muzilla.db.models import ApplyRun, ReviewUndoRun

    if job_type == "apply_review_bundle":
        run_id_key = "apply_run_id"
        committed_state = "applied"
    elif job_type == "undo_review_bundle":
        run_id_key = "undo_run_id"
        committed_state = "undone"
    else:
        return None

    raw_run_id = payload.get(run_id_key)
    if not isinstance(raw_run_id, int | str):
        return None
    try:
        run_id = int(raw_run_id)
    except ValueError:
        return None

    with session_factory() as session:
        if job_type == "apply_review_bundle":
            apply_run = session.get(ApplyRun, run_id)
            if (
                apply_run is None
                or apply_run.state != committed_state
                or not isinstance(apply_run.result, dict)
            ):
                return None
            result = dict(apply_run.result)
            review_bundle_id = apply_run.review_bundle_id
            source_apply_run_id: int | None = None
        else:
            undo_run = session.get(ReviewUndoRun, run_id)
            if (
                undo_run is None
                or undo_run.state != committed_state
                or not isinstance(undo_run.result, dict)
            ):
                return None
            result = dict(undo_run.result)
            review_bundle_id = undo_run.review_bundle_id
            source_apply_run_id = undo_run.source_apply_run_id

        if result.get("state") != committed_state:
            return None
        result[run_id_key] = run_id
        result["review_bundle_id"] = review_bundle_id
        if job_type == "apply_review_bundle":
            errors: dict[int, str] = {}
            files = result.get("files")
            if isinstance(files, list):
                for file_result in files:
                    if not isinstance(file_result, dict):
                        continue
                    track_id = file_result.get("track_id")
                    error = file_result.get("error")
                    if isinstance(track_id, int) and isinstance(error, str):
                        errors[track_id] = error
            result["errors"] = errors
        else:
            assert source_apply_run_id is not None
            result["source_apply_run_id"] = source_apply_run_id
        return result


async def _execute(
    session_factory: sessionmaker[Session],
    job_id: int,
    worker_id: str,
    attempts: int,
    handler: JobHandler,
    config: JobsConfig,
    context: WorkerContext,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    provider_lease = (
        context.provider_runtime.acquire() if context.provider_runtime is not None else None
    )
    execution_context = replace(
        context,
        provider_set=provider_lease.provider_set
        if provider_lease is not None
        else context.provider_set,
        session_factory=session_factory,
    )
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(
            session_factory,
            job_id,
            worker_id=worker_id,
            attempts=attempts,
            lease_seconds=config.lease_seconds,
        )
    )
    stop_task = asyncio.create_task(stop_event.wait()) if stop_event is not None else None
    handler_task: asyncio.Task[dict[str, object]] | None = None
    caller_cancelled = False
    try:
        with session_factory() as session:
            job = session.get(Job, job_id)
            assert job is not None
            with job_context(job_id):
                _logger.info("job start", extra={"job_type": job.type})
            reporter = ProgressReporter(session, job_id, coalesce_ms=config.event_coalesce_ms)
            token = CancellationToken(
                session_factory,
                job_id,
                poll_seconds=config.cancel_poll_seconds,
            )

            async def invoke_handler() -> dict[str, object]:
                return await handler(session, job, reporter, execution_context)

            with job_context(job_id), bind_token(token):
                handler_task = asyncio.create_task(invoke_handler())
            waiters = (handler_task, stop_task) if stop_task is not None else (handler_task,)
            try:
                done, _ = await asyncio.wait(
                    waiters,
                    timeout=config.job_timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                handler_completed = handler_task in done
                stopped = stop_task is not None and stop_task in done
            except asyncio.CancelledError:
                # A cancelled run_one/_execute task is a shutdown request too:
                # retain all resources until its handler has acknowledged it.
                caller_cancelled = True
                handler_completed = False
                stopped = False
            timed_out = not caller_cancelled and not handler_completed and not stopped
            if not handler_completed and (timed_out or stopped or caller_cancelled):
                _, cancelled_during_write = await _run_supervised_sync(
                    _request_cancel_in_session, session_factory, job_id
                )
                caller_cancelled |= cancelled_during_write
                handler_task.cancel()

            if stop_task is not None:
                if not stop_task.done():
                    stop_task.cancel()
                caller_cancelled |= await _drain_task(stop_task)

            result: dict[str, object] | None = None
            cancellation_result: dict[str, object] | None = None
            failure: BaseException | None = None
            caller_cancelled |= await _drain_task(handler_task)
            try:
                result = handler_task.result()
            except JobCancelled as exc:
                cancellation_result = exc.result
            except asyncio.CancelledError as exc:
                failure = exc
            except Exception as exc:  # handler failure must not kill the worker loop
                failure = exc

            # The file and run-state commit is authoritative even when the
            # handler raises or cancellation interrupts construction of its response.
            durable_result = _durable_review_result(session_factory, job.type, job.payload)
            if durable_result is not None:
                result = durable_result
                cancellation_result = None
                failure = None

            reporter.flush()
            committed = isinstance(result, dict) and result.get("state") in {"applied", "undone"}
            if committed and result is not None:
                succeeded = queue.mark_succeeded(
                    session,
                    job_id,
                    result,
                    worker_id=worker_id,
                    attempts=attempts,
                )
                outcome = "succeeded" if succeeded else "lost_lease"
            elif timed_out:
                queue.mark_failed(
                    session,
                    job_id,
                    f"job timed out after {config.job_timeout_seconds}s",
                    worker_id=worker_id,
                    attempts=attempts,
                    fail_even_if_cancel_requested=True,
                )
                outcome = "failed"
            elif (
                stopped
                or caller_cancelled
                or token.is_requested(force=True)
                or cancellation_result is not None
            ):
                cancel_result = cancellation_result or result
                if cancel_result is not None:
                    cancel_result = {**cancel_result, "partial": True}
                with contextlib.suppress(Exception):
                    session.rollback()
                queue.mark_cancelled(
                    session,
                    job_id,
                    cancel_result,
                    worker_id=worker_id,
                    attempts=attempts,
                )
                outcome = "cancelled"
            elif failure is not None:
                queue.mark_failed(
                    session,
                    job_id,
                    str(failure),
                    worker_id=worker_id,
                    attempts=attempts,
                )
                outcome = "failed"
                if not isinstance(failure, asyncio.CancelledError):
                    _logger.warning(
                        "job handler failed",
                        extra={"job_type": job.type, "outcome": outcome},
                        exc_info=failure,
                    )
            elif result is not None:
                succeeded = queue.mark_succeeded(
                    session,
                    job_id,
                    result,
                    worker_id=worker_id,
                    attempts=attempts,
                )
                outcome = "succeeded" if succeeded else "cancelled"
            else:
                queue.mark_failed(
                    session,
                    job_id,
                    "job handler returned no result",
                    worker_id=worker_id,
                    attempts=attempts,
                )
                outcome = "failed"
            with job_context(job_id):
                _logger.info("job end", extra={"job_type": job.type, "outcome": outcome})
    finally:
        if handler_task is not None and not handler_task.done():
            try:
                _, cancelled_during_write = await _run_supervised_sync(
                    _request_cancel_in_session, session_factory, job_id
                )
                caller_cancelled |= cancelled_during_write
            except Exception:
                _logger.exception("failed to request cancellation while draining job handler")
            handler_task.cancel()
            caller_cancelled |= await _drain_task(handler_task)
        try:
            if stop_task is not None:
                if not stop_task.done():
                    stop_task.cancel()
                caller_cancelled |= await _drain_task(stop_task)
                with contextlib.suppress(asyncio.CancelledError):
                    stop_task.result()
        finally:
            try:
                if not heartbeat_task.done():
                    heartbeat_task.cancel()
                caller_cancelled |= await _drain_task(heartbeat_task)
                with contextlib.suppress(asyncio.CancelledError):
                    heartbeat_task.result()
            finally:
                if provider_lease is not None:
                    release_task = asyncio.create_task(provider_lease.release())
                    caller_cancelled |= await _drain_task(release_task)
                    release_task.result()
    if caller_cancelled:
        raise asyncio.CancelledError


async def _heartbeat_loop(
    session_factory: sessionmaker[Session],
    job_id: int,
    *,
    worker_id: str,
    attempts: int,
    lease_seconds: int,
) -> None:
    """Renews the job's lease periodically while a handler runs, so a
    long-running stage isn't reclaimed by periodic crash recovery."""
    interval = max(lease_seconds / 3, 1)
    while True:
        await asyncio.sleep(interval)
        heartbeat_ok: bool
        cancelled: bool
        heartbeat_ok, cancelled = await _run_supervised_sync(
            _heartbeat_in_session,
            session_factory,
            job_id,
            worker_id,
            attempts,
            lease_seconds,
        )
        if cancelled:
            raise asyncio.CancelledError
        if not heartbeat_ok:
            return


async def run_forever(
    session_factory: sessionmaker[Session],
    *,
    worker_id: str,
    config: JobsConfig,
    stop_event: asyncio.Event,
    context: WorkerContext,
) -> None:
    while not stop_event.is_set():
        ran = await run_one(
            session_factory,
            worker_id=worker_id,
            config=config,
            context=context,
            stop_event=stop_event,
        )
        if not ran:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=config.poll_interval_seconds)


def _recover_expired_jobs(
    session_factory: sessionmaker[Session], library_root: Path | None = None
) -> None:
    with session_factory() as session:
        queue.recover_stuck_jobs(session, library_root=library_root)


async def run_lease_recovery_loop(
    session_factory: sessionmaker[Session],
    *,
    stop_event: asyncio.Event,
    interval_seconds: float,
    library_root: Path | None = None,
) -> None:
    """Periodically reconcile expired leases through their execution locks."""
    from muzilla.jobs.sync import run_sync

    while not stop_event.is_set():
        await run_sync(_recover_expired_jobs, session_factory, library_root)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)


async def run_retention_loop(
    session_factory: sessionmaker[Session],
    *,
    stop_event: asyncio.Event,
    context: WorkerContext,
    run_immediately: bool = True,
) -> None:
    """Enqueues a `retention_sweep` job once immediately on startup and then
    every `retention.sweep_interval_hours` until stopped. Deliberately just
    an asyncio.sleep loop rather than a scheduler dependency.
    A no-op entirely when effective `retention.enabled` is False (env > DB > base)."""
    from muzilla.pipeline.effective_settings import effective_retention_config

    with session_factory() as _s:
        _eff = effective_retention_config(_s, context.config.retention)
        if not _eff.enabled:
            return
        interval_seconds = _eff.sweep_interval_hours * 3600
    if not run_immediately:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        if stop_event.is_set():
            return
    while True:
        # do-while shape, deliberately: the startup sweep must run even
        # if stop_event is already set by the time this task gets
        # scheduled (a fast shutdown racing startup) — a plain
        # `while not stop_event.is_set()` guard would silently skip it,
        # breaking "runs once at startup" for exactly the shutdown-soon
        # case where catching up on retention matters least but the
        # guarantee should still hold.
        with session_factory() as session:
            try:
                queue.enqueue(session, type="retention_sweep", payload={})
            except queue.MaintenanceModeError:
                return
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
    retention_startup: bool = True,
) -> None:
    """Spawns `config.worker_concurrency` independent run_forever loops
    sharing one stop_event, each with a distinct worker_id, plus one
    retention-sweep loop. Bounded concurrency via N
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
    workers.append(
        run_retention_loop(
            session_factory,
            stop_event=stop_event,
            context=context,
            run_immediately=retention_startup,
        )
    )
    workers.append(
        run_lease_recovery_loop(
            session_factory,
            stop_event=stop_event,
            interval_seconds=config.poll_interval_seconds,
            library_root=context.config.storage.library_root,
        )
    )
    await asyncio.gather(*workers)
