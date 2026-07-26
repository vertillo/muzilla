from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, get_handler, register, registered_types


def test_register_and_get_handler() -> None:
    @register("test_noop")
    async def handle_noop(
        session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
    ) -> dict[str, object]:
        return {"ok": True}

    handler = get_handler("test_noop")
    assert handler is handle_noop
    assert "test_noop" in registered_types()


def test_get_handler_unknown_type_raises_key_error() -> None:
    with pytest.raises(KeyError):
        get_handler("definitely_not_a_registered_type")
