"""Structured JSON logging (docs/PLAN.md §11d).

Call `configure_logging(config)` once, from api/app.py's lifespan or the
CLI entry point — every other module just uses stdlib
`logging.getLogger(__name__)` as normal; this module only owns the
root handler/formatter setup and the job_id/change_set_id context
plumbing.

Named `logging.py` at the package root deliberately: Python 3's imports
are absolute by default (no implicit relative import of a same-named
module), so `import logging` from anywhere in this package still reaches
the stdlib module, not this file — the two coexist without shadowing.
"""

from __future__ import annotations

import json
import logging as _stdlib_logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from muzilla.config.schema import LoggingConfig

_job_id_var: ContextVar[int | None] = ContextVar("muzilla_job_id", default=None)
_change_set_id_var: ContextVar[int | None] = ContextVar("muzilla_change_set_id", default=None)

_RESERVED_LOG_RECORD_ATTRS = frozenset(_stdlib_logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
"""Every attribute a fresh LogRecord already has — anything else on a
record came from `extra={...}` and belongs in the JSON payload, not
duplicated as a top-level formatter concern."""


class _JsonFormatter(_stdlib_logging.Formatter):
    def format(self, record: _stdlib_logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        job_id = _job_id_var.get()
        if job_id is not None:
            payload["job_id"] = job_id
        change_set_id = _change_set_id_var.get()
        if change_set_id is not None:
            payload["change_set_id"] = change_set_id

        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS:
                payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(config: LoggingConfig) -> None:
    """Idempotent: safe to call more than once (tests do), replaces
    the root handler rather than stacking a duplicate one each time."""
    root = _stdlib_logging.getLogger()
    root.setLevel(config.level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = _stdlib_logging.StreamHandler()
    if config.json_output:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            _stdlib_logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
    root.addHandler(handler)


@contextmanager
def job_context(job_id: int) -> Iterator[None]:
    """Every log record emitted inside this block (any logger, any
    module) carries job_id — set once around a job's execution
    (jobs/worker.py::_execute), not threaded through every call site."""
    token = _job_id_var.set(job_id)
    try:
        yield
    finally:
        _job_id_var.reset(token)


@contextmanager
def change_set_context(change_set_id: int) -> Iterator[None]:
    """Same pattern as job_context, for the apply/undo job handlers
    specifically — nests inside an outer job_context so both ids are
    present on records emitted during an apply."""
    token = _change_set_id_var.set(change_set_id)
    try:
        yield
    finally:
        _change_set_id_var.reset(token)
