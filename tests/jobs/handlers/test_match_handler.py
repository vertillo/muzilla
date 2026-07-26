from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from muzilla.db.models import ChangeSet, ImportSession, Track, TrackGroup
from muzilla.jobs.handlers.match import handle_match
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate, ReleaseQuery
from muzilla.providers.set import ProviderSet


@dataclass
class StubProvider:
    releases: dict[str, ReleaseCandidate] = field(default_factory=dict)
    search_results: list[ReleaseCandidate] = field(default_factory=list)

    async def search_releases(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]:
        return self.search_results[:limit]

    async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None:
        return self.releases.get(ref.id)


def _release() -> ReleaseCandidate:
    return ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id="release-1"),
        album="Ágætis byrjun",
        album_artist="Sigur Rós",
        year=1999,
        label="Fat Cat Records",
        mb_release_id="release-1",
        tracks=(
            CandidateTrack(position=1, title="Intro", duration_ms=100_000),
            CandidateTrack(position=2, title="Svefn-g-englar", duration_ms=600_000),
        ),
    )


def _context() -> WorkerContext:
    stub = StubProvider(releases={"release-1": _release()}, search_results=[_release()])
    return WorkerContext(
        provider_set=ProviderSet(
            metadata={"musicbrainz": stub},  # type: ignore[dict-item]
            art={},
            lyrics={},
            fingerprint={},
            clients=(),
        )
    )


def _make_track(session: Session, *, path: str, **kwargs: object) -> Track:
    t = Track(path=path, filename=path.rsplit("/", 1)[-1], ext=".mp3", size_bytes=1000, mtime_ns=1, **kwargs)
    session.add(t)
    session.flush()
    return t


async def test_handle_match_stages_album_group(db_session: Session) -> None:
    t1 = _make_track(
        db_session, path="/a1", title="Intro", album="Agaetis byrjun",
        album_artist="Sigur Ros", duration_ms=100_000,
    )
    t2 = _make_track(
        db_session, path="/a2", title="Svefn-g-englar", album="Agaetis byrjun",
        album_artist="Sigur Ros", duration_ms=600_000,
    )
    group = TrackGroup(key="k1", kind="album", album="Agaetis byrjun", album_artist="Sigur Ros")
    db_session.add(group)
    db_session.flush()
    t1.group_id = group.id
    t2.group_id = group.id
    db_session.commit()

    job = enqueue(db_session, type="match", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_match(db_session, job, progress, _context())

    assert result["proposed"] == 1
    db_session.expire_all()
    refreshed_group = db_session.get(TrackGroup, group.id)
    assert refreshed_group is not None
    assert refreshed_group.match_state == "proposed"
    changesets = list(db_session.query(ChangeSet).all())
    assert len(changesets) == 1
    assert changesets[0].candidate_source == "musicbrainz"


async def test_handle_match_stages_singleton_track(db_session: Session) -> None:
    t = _make_track(db_session, path="/s1", title="Intro", duration_ms=100_000)
    group = TrackGroup(key="k2", kind="singleton")
    db_session.add(group)
    db_session.flush()
    t.group_id = group.id
    db_session.commit()

    job = enqueue(db_session, type="match", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_match(db_session, job, progress, _context())

    assert result["proposed"] == 1
    changesets = list(db_session.query(ChangeSet).all())
    assert len(changesets) == 1
    assert changesets[0].scope_type == "track"
    assert changesets[0].scope_id == t.id


async def test_handle_match_tags_changeset_with_import_session_id(db_session: Session) -> None:
    t = _make_track(db_session, path="/s2", title="Intro", duration_ms=100_000)
    group = TrackGroup(key="k3", kind="singleton")
    import_session = ImportSession(library_root="/music", stats={})
    db_session.add_all([group, import_session])
    db_session.flush()
    t.group_id = group.id
    db_session.commit()

    job = enqueue(
        db_session, type="match", payload={"import_session_id": import_session.id}
    )
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    await handle_match(db_session, job, progress, _context())

    changesets = list(db_session.query(ChangeSet).all())
    assert len(changesets) == 1
    assert changesets[0].import_session_id == import_session.id
