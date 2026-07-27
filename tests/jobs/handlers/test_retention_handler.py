from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import ApplyJournal, ChangeSet, Track
from muzilla.jobs.handlers.retention import handle_retention_sweep
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.set import ProviderSet


def _context(config: Config | None = None) -> WorkerContext:
    return WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=config or Config(),
    )


def _make_track(session: Session) -> Track:
    t = Track(path="/a.mp3", filename="a.mp3", ext="mp3", size_bytes=1000, mtime_ns=1)
    session.add(t)
    session.flush()
    return t


async def test_handle_retention_sweep_prunes_old_journal(db_session: Session) -> None:
    track = _make_track(db_session)
    cs = ChangeSet(title="Edit", source="manual_edit", state="applied")
    db_session.add(cs)
    db_session.flush()
    journal = ApplyJournal(
        change_set_id=cs.id, track_id=track.id, path=track.path, phase="tags",
        state="done", before_blob={},
    )
    db_session.add(journal)
    db_session.flush()
    journal.created_at = datetime.now(UTC) - timedelta(days=31)
    db_session.commit()

    job = enqueue(db_session, type="retention_sweep", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    config = Config(retention={"journal_days": 30, "journal_changesets": 500})
    result = await handle_retention_sweep(db_session, job, progress, _context(config))

    assert result == {
        "journals_pruned": 1,
        "changesets_marked_expired": 1,
        "provider_cache_rows_pruned": 0,
    }
    db_session.expire_all()
    assert db_session.query(ApplyJournal).count() == 0
    assert db_session.get(ChangeSet, cs.id).state == "undo_expired"


async def test_handle_retention_sweep_uses_context_config_thresholds(db_session: Session) -> None:
    """Proves retention.journal_days/journal_changesets actually reach
    pipeline/retention.py through the handler, not just that a sweep
    with defaults works — a journal 10 days old survives the default
    30-day threshold but must be pruned when config sets it to 5."""
    track = _make_track(db_session)
    cs = ChangeSet(title="Edit", source="manual_edit", state="applied")
    db_session.add(cs)
    db_session.flush()
    journal = ApplyJournal(
        change_set_id=cs.id, track_id=track.id, path=track.path, phase="tags",
        state="done", before_blob={},
    )
    db_session.add(journal)
    db_session.flush()
    journal.created_at = datetime.now(UTC) - timedelta(days=10)
    db_session.commit()

    job = enqueue(db_session, type="retention_sweep", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    config = Config(retention={"journal_days": 5, "journal_changesets": 500})
    result = await handle_retention_sweep(db_session, job, progress, _context(config))

    assert result["journals_pruned"] == 1
