"""Cooperative cancellation backed by the persisted job row.

Handlers must not inspect the ``Job`` instance they were handed at lease
time: API cancellation happens in another SQLAlchemy session and this project
intentionally uses ``expire_on_commit=False``.  A token therefore performs a
small, throttled read through a fresh session at each safe checkpoint.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import Job

_active_token: ContextVar[CancellationToken | None] = ContextVar("active_cancellation_token", default=None)


class CancellationToken:
    """Reads cancellation state from the database, never from a stale ORM row."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Session]],
        job_id: int,
        *,
        poll_seconds: float = 0.05,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_scope = session_scope
        self._job_id = job_id
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._last_checked: float | None = None
        self._requested = False

    @classmethod
    def from_session(cls, session: Session, job_id: int) -> CancellationToken:
        """Fallback for direct handler tests and synchronous callers.

        The SELECT still reads the persisted column rather than an attribute on
        the caller's already-loaded ``Job`` object.
        """

        return cls(lambda: nullcontext(session), job_id, poll_seconds=0)

    def is_requested(self, *, force: bool = False) -> bool:
        if self._requested:
            return True
        now = self._clock()
        if not force and self._last_checked is not None and now - self._last_checked < self._poll_seconds:
            return False
        self._last_checked = now
        with self._session_scope() as session:
            row = session.execute(
                select(Job.cancel_requested, Job.state).where(Job.id == self._job_id)
            ).one_or_none()
        self._requested = row is None or bool(row.cancel_requested) or row.state in {
            "cancelling",
            "cancelled",
        }
        return self._requested


def current_token(session: Session, job_id: int) -> CancellationToken:
    """Returns the worker token for this execution, or a direct-call fallback."""

    return _active_token.get() or CancellationToken.from_session(session, job_id)


@contextmanager
def bind_token(token: CancellationToken) -> Iterator[None]:
    reset = _active_token.set(token)
    try:
        yield
    finally:
        _active_token.reset(reset)


__all__ = ["CancellationToken", "bind_token", "current_token"]
