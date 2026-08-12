"""Explicit job type-to-handler registry.

Handlers register via ``@register("type")`` at import time; worker startup
must import the handler modules before dispatching jobs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.providers.runtime import ProviderSetRuntime
from muzilla.providers.set import ProviderSet


@dataclass(frozen=True, slots=True)
class WorkerContext:
    """Process-wide resources every handler may need, built once by
    whoever starts the worker pool (api/app.py's lifespan, or the CLI's
    `jobs worker` command) — mirrors how ProviderSet itself is already
    a per-process singleton for API request handlers."""

    provider_set: ProviderSet
    config: Config
    # Pins the provider set for the full leased job when present.
    provider_runtime: ProviderSetRuntime | None = None


JobHandler = Callable[[Session, Job, ProgressReporter, WorkerContext], Awaitable[dict[str, object]]]
"""Receives the worker-owned session, the leased Job row, a bound
ProgressReporter, and the shared WorkerContext. Returns the JSON-able
`result` dict on success; raises on failure (the worker's supervisor
catches it and calls queue.mark_failed)."""

_REGISTRY: dict[str, JobHandler] = {}


def register(job_type: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(handler: JobHandler) -> JobHandler:
        _REGISTRY[job_type] = handler
        return handler

    return decorator


def get_handler(job_type: str) -> JobHandler:
    try:
        return _REGISTRY[job_type]
    except KeyError:
        raise KeyError(f"no job handler registered for type {job_type!r}") from None


def registered_types() -> tuple[str, ...]:
    return tuple(_REGISTRY.keys())
