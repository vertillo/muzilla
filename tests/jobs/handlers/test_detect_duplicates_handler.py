from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import DuplicateGroup, Track, TrackFingerprintMatch
from muzilla.jobs.handlers.detect_duplicates import handle_detect_duplicates
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.set import ProviderSet


def _context() -> WorkerContext:
    return WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=Config(),
    )


def _make_track(session: Session, *, path: str) -> Track:
    t = Track(path=path, filename=path.rsplit("/", 1)[-1], ext=".mp3", size_bytes=1000, mtime_ns=1)
    session.add(t)
    session.flush()
    return t


async def test_handle_detect_duplicates_creates_groups(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/a-128.mp3")
    t2 = _make_track(db_session, path="/a-320.mp3")
    db_session.add(TrackFingerprintMatch(track_id=t1.id, mb_recording_id="rec-1", mb_release_ids=[], score=0.9))
    db_session.add(TrackFingerprintMatch(track_id=t2.id, mb_recording_id="rec-1", mb_release_ids=[], score=0.9))
    db_session.commit()

    job = enqueue(db_session, type="detect_duplicates", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_detect_duplicates(db_session, job, progress, _context())

    assert result["groups_created"] == 1
    db_session.expire_all()
    assert db_session.query(DuplicateGroup).count() == 1


async def test_handle_detect_duplicates_no_duplicates(db_session: Session) -> None:
    job = enqueue(db_session, type="detect_duplicates", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_detect_duplicates(db_session, job, progress, _context())

    assert result == {
        "groups_created": 0,
        "groups_updated": 0,
        "groups_dismissed_skipped": 0,
        "groups_removed": 0,
    }
