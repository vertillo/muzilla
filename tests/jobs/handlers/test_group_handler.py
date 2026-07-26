from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import Track, TrackGroup
from muzilla.jobs.handlers.group import handle_group
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.set import ProviderSet


def _context() -> WorkerContext:
    return WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=Config(),
    )


def _make_track(session: Session, *, path: str, **kwargs: object) -> Track:
    t = Track(path=path, filename=path.rsplit("/", 1)[-1], ext=".mp3", size_bytes=1000, mtime_ns=1, **kwargs)
    session.add(t)
    session.flush()
    return t


async def test_handle_group_creates_album_group(db_session: Session) -> None:
    mbid = "11111111-1111-1111-1111-111111111111"
    _make_track(db_session, path="/a1", title="T1", album="X", mb_release_id=mbid)
    _make_track(db_session, path="/a2", title="T2", album="X", mb_release_id=mbid)
    db_session.commit()

    job = enqueue(db_session, type="group", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_group(db_session, job, progress, _context())

    assert isinstance(result["groups_created"], int)
    assert result["groups_created"] >= 1
    groups = list(db_session.query(TrackGroup).all())
    assert len(groups) >= 1
