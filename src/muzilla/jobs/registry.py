"""Job type -> handler mapping.

A plain dict-based registry, not a beets-style event bus (CLAUDE.md:
"explicit Protocol-based registries" over implicit dispatch). Handlers
register themselves via the `@register("type")` decorator at import
time; `jobs/handlers/*` modules must be imported somewhere on the
process's startup path (the worker module does this) for their
registrations to take effect.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.providers.set import ProviderSet


@dataclass(frozen=True, slots=True)
class WorkerContext:
    """Process-wide resources every handler may need, built once by
    whoever starts the worker pool (api/app.py's lifespan, or the CLI's
    `jobs worker` command) — mirrors how ProviderSet itself is already
    a per-process singleton for API request handlers."""

    provider_set: ProviderSet
    config: Config
    """Passed explicitly rather than handlers calling load_config()
    themselves (CLAUDE.md: pass a Config object explicitly, listed as
    a beets anti-pattern to avoid otherwise) — needed by
    apply_changeset's move phase for library_root/create_directories."""


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
