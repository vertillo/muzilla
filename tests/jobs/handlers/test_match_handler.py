from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import ChangeSet, ImportSession, Track, TrackGroup
from muzilla.jobs.handlers import match as match_handler
from muzilla.jobs.handlers.match import handle_match
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.matching import CandidateRow, TrackMatchProposal
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
        ),
        config=Config(),
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


async def test_handle_match_discards_proposal_when_cancel_arrives_during_provider_fetch(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = _make_track(db_session, path="/cancelled", title="Intro", duration_ms=100_000)
    group = TrackGroup(key="cancelled-singleton", kind="singleton")
    db_session.add(group)
    db_session.flush()
    track.group_id = group.id
    db_session.commit()

    job = enqueue(db_session, type="match", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)
    candidate = CandidateRow(
        source="musicbrainz",
        ref_id="release-1",
        album="Album",
        album_artist="Artist",
        year=None,
        label=None,
        catalog_number=None,
        track_count=1,
        distance=0.0,
        adjusted_distance=0.0,
        is_duplicate_of=(),
        corroborated_by=(),
    )

    async def cancelled_proposal(*args: object, **kwargs: object) -> TrackMatchProposal:
        job.cancel_requested = True
        db_session.commit()
        return TrackMatchProposal(
            track_id=track.id,
            candidates=(candidate,),
            auto_applicable=True,
            needs_confirmation=False,
        )

    async def stage_must_not_run(*args: object, **kwargs: object) -> ChangeSet:
        raise AssertionError("a cancelled fetch must not stage a proposal")

    monkeypatch.setattr(match_handler, "propose_track_candidates", cancelled_proposal)
    monkeypatch.setattr(match_handler, "stage_track_match", stage_must_not_run)

    with pytest.raises(JobCancelled) as exc_info:
        await handle_match(db_session, job, progress, _context())

    assert exc_info.value.result == {
        "proposed": 0,
        "skipped_no_candidates": 0,
        "partial": True,
    }
    db_session.expire_all()
    refreshed_group = db_session.get(TrackGroup, group.id)
    assert refreshed_group is not None
    assert refreshed_group.match_state == "unmatched"
    assert db_session.query(ChangeSet).count() == 0
