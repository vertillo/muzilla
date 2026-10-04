"""Supervised synchronous work for the asynchronous job worker.

The executor thread owns its SQLAlchemy session. A cancelled waiter joins the
underlying thread before propagating cancellation, because cancelling
``asyncio.to_thread`` does not stop the callable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress

from sqlalchemy.orm import Session, sessionmaker


async def run_sync[T](function: Callable[..., T], /, *args: object, **kwargs: object) -> T:
    """Run a synchronous callable without abandoning it on task cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Keep the caller alive until all work in the executor thread has
        # stopped. Repeated cancellation requests must not abandon the thread.
        # The worker owns the job lock, heartbeat and other resources until
        # this join completes, and a completed result remains available to it.
        while not task.done():
            with suppress(asyncio.CancelledError):
                await asyncio.shield(task)
        return task.result()


def session_factory_for(
    session_factory: sessionmaker[Session] | None, session: Session
) -> sessionmaker[Session]:
    """Use the worker's factory, or create an independent one for direct tests."""
    return session_factory or sessionmaker(
        bind=session.get_bind(), autoflush=False, expire_on_commit=False
    )


async def run_with_session[T](
    session_factory: sessionmaker[Session] | None,
    fallback_session: Session,
    function: Callable[..., T],
    /,
    *args: object,
    **kwargs: object,
) -> T:
    """Run a database callable with a session created and closed in its thread."""
    factory = session_factory_for(session_factory, fallback_session)

    def invoke() -> T:
        with factory() as session:
            return function(session, *args, **kwargs)

    return await run_sync(invoke)
