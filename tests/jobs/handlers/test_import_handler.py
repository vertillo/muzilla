from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from muzilla.db.models import ImportSession, ImportTask, Track
from muzilla.jobs.handlers import import_session as import_session_handler
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.jobs.worker import JobCancelled
from muzilla.providers.set import ProviderSet

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "audio"

_STAGES = ("scan", "fingerprint", "group", "match")


def _context() -> WorkerContext:
    return WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=())
    )


def _make_session_with_tasks(session: Session, library_root: Path) -> ImportSession:
    import_session = ImportSession(library_root=str(library_root), stats={})
    session.add(import_session)
    session.flush()
    for i, stage in enumerate(_STAGES):
        session.add(ImportTask(import_session_id=import_session.id, stage=stage, seq=i))
    session.commit()
    return import_session


async def test_handle_import_runs_all_stages_in_order(
    db_session: Session, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    shutil.copy(FIXTURES / "silence.mp3", library / "silence.mp3")

    import_session = _make_session_with_tasks(db_session, library)
    job = enqueue(
        db_session, type="import", payload={"import_session_id": import_session.id}
    )
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await import_session_handler.handle_import(db_session, job, progress, _context())

    assert result["state"] == "reviewing"
    db_session.expire_all()
    tasks = sorted(
        db_session.query(ImportTask).filter_by(import_session_id=import_session.id),
        key=lambda t: t.seq,
    )
    assert [t.stage for t in tasks] == list(_STAGES)
    assert all(t.state == "done" for t in tasks)

    refreshed_session = db_session.get(ImportSession, import_session.id)
    assert refreshed_session is not None
    assert refreshed_session.state == "reviewing"

    tracks = list(db_session.query(Track).all())
    assert len(tracks) == 1


async def test_handle_import_resumes_skipping_done_stages(
    db_session: Session, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()

    import_session = _make_session_with_tasks(db_session, library)
    tasks = sorted(
        db_session.query(ImportTask).filter_by(import_session_id=import_session.id),
        key=lambda t: t.seq,
    )
    tasks[0].state = "done"
    tasks[0].result = {"scanned": 0}
    db_session.commit()

    job = enqueue(
        db_session, type="import", payload={"import_session_id": import_session.id}
    )
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    await import_session_handler.handle_import(db_session, job, progress, _context())

    db_session.expire_all()
    refreshed_scan_task = db_session.get(ImportTask, tasks[0].id)
    assert refreshed_scan_task is not None
    assert refreshed_scan_task.result == {"scanned": 0}  # untouched, not re-run


async def test_handle_import_raises_job_cancelled_when_requested(
    db_session: Session, tmp_path: Path
) -> None:
    import_session = _make_session_with_tasks(db_session, tmp_path / "library")
    job = enqueue(
        db_session, type="import", payload={"import_session_id": import_session.id}
    )
    job.cancel_requested = True
    db_session.commit()
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    with pytest.raises(JobCancelled):
        await import_session_handler.handle_import(db_session, job, progress, _context())

    db_session.expire_all()
    refreshed_session = db_session.get(ImportSession, import_session.id)
    assert refreshed_session is not None
    assert refreshed_session.state == "cancelled"


async def test_handle_import_unknown_session_raises(db_session: Session) -> None:
    job = enqueue(db_session, type="import", payload={"import_session_id": 99999})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    with pytest.raises(ValueError, match="not found"):
        await import_session_handler.handle_import(db_session, job, progress, _context())
