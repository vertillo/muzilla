"""Coalesced progress reporting for job handlers.

docs/product-spec.md: "Worker coalesces to <=1 event/250ms per job — else a
40k-file scan writes 40k rows." `Job.progress_current/total/message`
are cheap column writes and are never throttled — only the
`job_events` row (what SSE replays) is rate-limited, so `GET
/api/jobs/{id}` (a plain read) is always current even between ticks.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from muzilla.db.models import Job
from muzilla.jobs.queue import append_event


class ProgressReporter:
    def __init__(
        self,
        session: Session,
        job_id: int,
        *,
        coalesce_ms: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session = session
        self._job_id = job_id
        self._coalesce_seconds = coalesce_ms / 1000
        self._clock = clock
        self._last_emit: float | None = None
        self._pending: tuple[int, int | None, str | None] | None = None

    def update(self, current: int, total: int | None = None, message: str | None = None) -> None:
        job = self._session.get(Job, self._job_id)
        if job is not None:
            job.progress_current = current
            job.progress_total = total
            job.progress_message = message
            self._session.commit()

        now = self._clock()
        if self._last_emit is not None and (now - self._last_emit) < self._coalesce_seconds:
            self._pending = (current, total, message)
            return

        self._emit(current, total, message)
        self._last_emit = now
        self._pending = None

    def log(self, message: str) -> None:
        """Not coalesced — stage transitions/warnings are sparse."""
        append_event(self._session, self._job_id, "log", {"message": message})

    def flush(self) -> None:
        """Force-emits a pending coalesced update, so the final state
        of a stage is never dropped by the throttle window."""
        if self._pending is None:
            return
        current, total, message = self._pending
        self._emit(current, total, message)
        self._last_emit = self._clock()
        self._pending = None

    def _emit(self, current: int, total: int | None, message: str | None) -> None:
        append_event(
            self._session,
            self._job_id,
            "progress",
            {"current": current, "total": total, "message": message},
        )
