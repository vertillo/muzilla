from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import Track, WorkUnit
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
    t = Track(
        path=path,
        filename=path.rsplit("/", 1)[-1],
        ext=".mp3",
        size_bytes=1000,
        mtime_ns=1,
        **kwargs,
    )
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
    groups = list(db_session.query(WorkUnit).all())
    assert len(groups) >= 1


async def test_scoped_grouping_does_not_mutate_mixed_group(db_session: Session) -> None:
    """Scoped grouping must not split a pre-existing mixed group."""
    mbid = "22222222-2222-2222-2222-222222222222"
    t_in = _make_track(
        db_session, path="/library/subA/a1.mp3", title="T1", album="X", mb_release_id=mbid
    )
    t_out = _make_track(
        db_session, path="/library/subB/b1.mp3", title="T2", album="X", mb_release_id=mbid
    )
    mixed_group = WorkUnit(key="mixed-group", kind="album", album="X", mb_release_id=mbid)
    db_session.add(mixed_group)
    db_session.flush()
    t_in.work_unit_id = mixed_group.id
    t_out.work_unit_id = mixed_group.id
    # A fully scoped ungrouped track that could otherwise be clustered with t_in.
    t_scoped_new = _make_track(
        db_session, path="/library/subA/a2.mp3", title="T3", album="X", mb_release_id=mbid
    )
    db_session.commit()

    job = enqueue(db_session, type="group", payload={"root": "/library/subA"})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)
    await handle_group(db_session, job, progress, _context())

    db_session.expire_all()
    refreshed_in = db_session.get(Track, t_in.id)
    refreshed_out = db_session.get(Track, t_out.id)
    assert refreshed_in is not None and refreshed_out is not None
    # Mixed group membership must stay intact (both still together).
    assert refreshed_in.work_unit_id == mixed_group.id
    assert refreshed_out.work_unit_id == mixed_group.id
    # The new scoped track may be grouped, but must not have pulled t_in out.
    refreshed_new = db_session.get(Track, t_scoped_new.id)
    assert refreshed_new is not None
    assert refreshed_new.work_unit_id is not None
