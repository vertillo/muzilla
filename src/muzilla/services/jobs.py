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

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import Job, ReviewBundle, TaskAttempt

# Importing the handler modules registers them (each decorates its
# function with @register at import time) — this import exists purely
# for that side effect, so the registry is populated before any job
# can be leased. Required here (not just in jobs/worker.py) since
# services is the only legal entry point api/cli/app.py has into jobs.
from muzilla.jobs import queue
from muzilla.jobs.handlers import apply as _apply_handler  # noqa: F401
from muzilla.jobs.handlers import detect_duplicates as _detect_duplicates_handler  # noqa: F401
from muzilla.jobs.handlers import enrich_art as _enrich_art_handler  # noqa: F401
from muzilla.jobs.handlers import enrich_lyrics as _enrich_lyrics_handler  # noqa: F401
from muzilla.jobs.handlers import enrich_replaygain as _enrich_replaygain_handler  # noqa: F401
from muzilla.jobs.handlers import fingerprint as _fingerprint_handler  # noqa: F401
from muzilla.jobs.handlers import group as _group_handler  # noqa: F401
from muzilla.jobs.handlers import import_session as _import_session_handler  # noqa: F401
from muzilla.jobs.handlers import match as _match_handler  # noqa: F401
from muzilla.jobs.handlers import retention as _retention_handler  # noqa: F401
from muzilla.jobs.handlers import scan as _scan_handler  # noqa: F401
from muzilla.jobs.registry import WorkerContext
from muzilla.jobs.worker import run_one, start_worker_pool
from muzilla.providers.runtime import ProviderSetRuntime
from muzilla.providers.set import ProviderSet
from muzilla.services.db import get_session_factory
from muzilla.services.reviews import plan_task_attempt

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


class WorkerPoolController:
    """Start, quiesce, and restart the one in-process worker pool."""

    def __init__(
        self,
        config: Config,
        provider_set: ProviderSet,
        *,
        provider_runtime: ProviderSetRuntime | None = None,
    ) -> None:
        self._config = config
        self._provider_set = provider_set
        self._provider_runtime = provider_runtime
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._has_started = False

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    async def start(self) -> asyncio.Task[None]:
        async with self._lifecycle_lock:
            if self._task is not None and not self._task.done():
                return self._task
            self._stop_event = asyncio.Event()
            retention_startup = not self._has_started
            self._has_started = True
            self._task = asyncio.create_task(
                run_worker_pool(
                    self._config,
                    self._provider_set,
                    self._stop_event,
                    provider_runtime=self._provider_runtime,
                    retention_startup=retention_startup,
                )
            )
            return self._task

    async def quiesce(self) -> None:
        async with self._lifecycle_lock:
            task = self._task
            stop_event = self._stop_event
            if task is None:
                return
            if stop_event is not None:
                stop_event.set()
            await task

    async def shutdown(self) -> None:
        await self.quiesce()


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


def enqueue_track_rescan(session: Session, track_id: int) -> JobSummary:
    """Queue a file-only reread; this never contacts providers or writes media."""
    from muzilla.db.models import Track

    if session.get(Track, track_id) is None:
        raise LookupError(f"track {track_id} not found")
    job = queue.enqueue(session, type="rescan_track", payload={"track_id": track_id}, priority=-10)
    return _to_summary(job)


def enqueue_replaygain(session: Session, *, priority: int = -10) -> JobSummary:
    job = queue.enqueue(session, type="enrich_replaygain", payload={}, priority=priority)
    return _to_summary(job)


def enqueue_art(session: Session, *, priority: int = 0) -> JobSummary:
    job = queue.enqueue(session, type="enrich_art", payload={}, priority=priority)
    return _to_summary(job)


def enqueue_lyrics(
    session: Session,
    *,
    track_ids: list[int] | None = None,
    retry_of: int | None = None,
    priority: int = 0,
) -> JobSummary:
    payload: dict[str, object] = {}
    if track_ids is not None:
        payload["track_ids"] = track_ids
    if retry_of is not None:
        payload["retry_of"] = retry_of
    job = queue.enqueue(session, type="enrich_lyrics", payload=payload, priority=priority)
    return _to_summary(job)


def retry_failed_lyrics(session: Session, job_id: int) -> JobSummary:
    """Enqueues only retryable item failures from a completed lyrics job.

    A ``not_found`` result is a useful, stable absence and must not be folded
    into retries.  Permanent errors stay visible in the original job too;
    callers can correct configuration/credentials rather than spinning them.
    """

    job = queue.get_job(session, job_id)
    if job is None:
        raise LookupError(f"job {job_id} not found")
    if job.type != "enrich_lyrics" or job.result is None:
        raise ValueError("job has no retryable lyrics result")
    raw_ids = job.result.get("retryable_track_ids")
    if not isinstance(raw_ids, list):
        raise ValueError("job has no retryable lyrics result")
    track_ids = [track_id for track_id in raw_ids if isinstance(track_id, int)]
    if not track_ids:
        raise ValueError("job has no retryable lyrics result")
    return enqueue_lyrics(session, track_ids=track_ids, retry_of=job.id)


def retry_review_task(session: Session, review_bundle_id: int, *, kind: str) -> JobSummary:
    """Retry one optional section under its existing ReviewBundle identity."""
    if session.get(ReviewBundle, review_bundle_id) is None:
        raise LookupError(f"review bundle {review_bundle_id} not found")
    job_type = {
        "cover": "enrich_art",
        "lyrics": "enrich_lyrics",
        "replaygain": "enrich_replaygain",
    }.get(kind)
    if job_type is None:
        raise ValueError("retry is supported only for cover, lyrics, or replaygain")
    attempts = list(
        session.scalars(
            select(TaskAttempt)
            .where(
                TaskAttempt.review_bundle_id == review_bundle_id,
                TaskAttempt.kind == kind,
            )
            .order_by(TaskAttempt.item_key, TaskAttempt.attempt_no)
        )
    )
    latest: dict[str, TaskAttempt] = {}
    for attempt in attempts:
        latest[attempt.item_key] = attempt
    retryable_keys = tuple(
        item_key
        for item_key, attempt in latest.items()
        if attempt.state == "transient_failure"
    )
    if not retryable_keys:
        raise ValueError(f"review has no retryable {kind} task")
    job = queue.enqueue(
        session,
        type=job_type,
        payload={
            "review_bundle_id": review_bundle_id,
            "retry_section": kind,
            "item_keys": list(retryable_keys),
        },
        priority=-10 if kind == "replaygain" else 0,
        commit=False,
    )
    for item_key in retryable_keys:
        plan_task_attempt(
            session,
            review_bundle_id,
            kind=kind,
            item_key=item_key,
            job_id=job.id,
        )
    session.commit()
    return _to_summary(job)


def enqueue_duplicate_detection(session: Session) -> JobSummary:
    job = queue.enqueue(session, type="detect_duplicates", payload={})
    return _to_summary(job)


def enqueue_retention_sweep(session: Session) -> JobSummary:
    """On-demand trigger (docs/product-spec.md: `muzilla jobs retention`) —
    the worker pool also runs this automatically at startup and every
    `retention.sweep_interval_hours` (jobs/worker.py::run_retention_loop)."""
    job = queue.enqueue(session, type="retention_sweep", payload={})
    return _to_summary(job)


def get_job(session: Session, job_id: int) -> JobDetail | None:
    job = queue.get_job(session, job_id)
    return _to_detail(job) if job is not None else None


def list_jobs(
    session: Session,
    *,
    state: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
    include_system: bool = False,
) -> JobPage:
    items, next_cursor = queue.list_jobs(
        session, state=state, cursor=cursor, limit=limit, include_system=include_system
    )
    return JobPage(items=tuple(_to_summary(j) for j in items), next_cursor=next_cursor)


def list_job_events(session: Session, job_id: int, after: int) -> list[JobEventOut]:
    events = queue.list_events_after(session, job_id, after)
    return [JobEventOut(seq=e.seq, kind=e.kind, payload=dict(e.payload)) for e in events]


def request_job_cancel(session: Session, job_id: int) -> JobDetail:
    job = queue.request_cancel(session, job_id)
    if job is None:
        raise ValueError(f"job {job_id} not found")
    return _to_detail(job)


def recover_stuck_jobs(session: Session) -> int:
    """Startup-only: resets jobs left `running` with an expired lease —
    the trace of a worker that died before clean shutdown."""
    return queue.recover_stuck_jobs(session)


async def run_worker_pool(
    config: Config,
    provider_set: ProviderSet,
    stop_event: asyncio.Event,
    *,
    provider_runtime: ProviderSetRuntime | None = None,
    retention_startup: bool = True,
) -> None:
    """The only entry point api/app.py's lifespan and the CLI's `jobs
    worker` command use to start the worker pool — neither may import
    muzilla.jobs directly."""
    session_factory = get_session_factory(config)
    context = WorkerContext(
        provider_set=provider_set, config=config, provider_runtime=provider_runtime
    )
    await start_worker_pool(
        session_factory,
        config=config.jobs,
        stop_event=stop_event,
        context=context,
        retention_startup=retention_startup,
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
        ran = await run_one(
            session_factory, worker_id=worker_id, config=config.jobs, context=context
        )
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
    "enqueue_art",
    "enqueue_duplicate_detection",
    "enqueue_lyrics",
    "enqueue_replaygain",
    "enqueue_scan",
    "get_job",
    "list_job_events",
    "list_jobs",
    "recover_stuck_jobs",
    "request_job_cancel",
    "retry_failed_lyrics",
    "retry_review_task",
    "run_job_once",
    "run_worker_pool",
]
