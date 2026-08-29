from __future__ import annotations

from dataclasses import dataclass, field, replace

import pytest
from sqlalchemy.orm import Session

from muzilla.db.models import Track, TrackGroup
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate, ReleaseQuery
from muzilla.services import matching as matching_service
from muzilla.services.providers import ProviderSet


@dataclass
class StubProvider:
    """A MetadataProvider stub returning fixed candidates, so tests
    never touch real providers/httpx."""

    releases: dict[str, ReleaseCandidate] = field(default_factory=dict)
    search_results: list[ReleaseCandidate] = field(default_factory=list)

    async def search_releases(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]:
        return self.search_results[:limit]

    async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None:
        return self.releases.get(ref.id)


def _make_track(session: Session, *, path: str, **kwargs: object) -> Track:
    t = Track(path=path, filename=path.rsplit("/", 1)[-1], ext=".mp3", size_bytes=1000, mtime_ns=1, **kwargs)  # type: ignore[call-arg]
    session.add(t)
    session.flush()
    return t


def _sigur_ros_release() -> ReleaseCandidate:
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


@pytest.fixture
def provider_set() -> ProviderSet:
    release = _sigur_ros_release()
    stub = StubProvider(
        releases={"release-1": release},
        search_results=[replace(release, tracks=(), track_count=2)],
    )
    return ProviderSet(metadata={"musicbrainz": stub}, art={}, lyrics={}, fingerprint={}, clients=())  # type: ignore[dict-item]


async def test_propose_group_candidates_ranks_matching_release(
    db_session: Session, provider_set: ProviderSet
) -> None:
    _make_track(db_session, path="/a1", title="Intro", album="Agaetis byrjun", album_artist="Sigur Ros", duration_ms=100_000)
    _make_track(db_session, path="/a2", title="Svefn-g-englar", album="Agaetis byrjun", album_artist="Sigur Ros", duration_ms=600_000)
    db_session.commit()

    group = TrackGroup(key="k1", album="Agaetis byrjun", album_artist="Sigur Ros")
    db_session.add(group)
    db_session.flush()
    for t in db_session.query(Track).all():
        t.group_id = group.id
    db_session.commit()

    result = await matching_service.propose_group_candidates(db_session, provider_set, group.id)
    assert len(result.candidates) == 1
    assert result.candidates[0].source == "musicbrainz"
    assert result.candidates[0].track_count == 2
    assert result.auto_applicable


async def test_propose_group_candidates_unknown_group_raises(
    db_session: Session, provider_set: ProviderSet
) -> None:
    with pytest.raises(ValueError, match="not found"):
        await matching_service.propose_group_candidates(db_session, provider_set, 99999)


async def test_propose_track_candidates_for_singleton(
    db_session: Session, provider_set: ProviderSet
) -> None:
    t = _make_track(db_session, path="/s1", title="Intro", artist="Sigur Rós", duration_ms=100_000)
    db_session.commit()

    result = await matching_service.propose_track_candidates(db_session, provider_set, t.id)
    assert len(result.candidates) == 1
    assert result.candidates[0].source == "musicbrainz"


async def test_propose_track_candidates_falls_back_to_high_confidence_filename(
    db_session: Session,
) -> None:
    release = ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id="piki"),
        album="Twilight",
        album_artist="Piki",
        track_count=1,
        tracks=(CandidateTrack(position=1, title="Twilight Twilight", artist="Piki"),),
    )
    provider_set = ProviderSet(
        metadata={"musicbrainz": StubProvider(releases={"piki": release}, search_results=[release])},  # type: ignore[dict-item]
        art={}, lyrics={}, fingerprint={}, clients=(),
    )
    t = _make_track(db_session, path="/singles/Piki - Twilight Twilight.mp3")
    db_session.commit()

    result = await matching_service.propose_track_candidates(db_session, provider_set, t.id)
    assert len(result.candidates) == 1
    assert result.candidates[0].representative_title == "Twilight Twilight"


async def test_unrelated_first_provider_hit_is_not_proposed(
    db_session: Session,
) -> None:
    bad = ReleaseCandidate(
        source="deezer",
        ref=ProviderRef(provider="deezer", id="kawai"),
        album="Kawai Kawai",
        album_artist="Kawai Kawai",
        track_count=1,
        tracks=(CandidateTrack(position=1, title="Kawai Kawai", duration_ms=241_000),),
    )
    provider_set = ProviderSet(
        metadata={"deezer": StubProvider(releases={"kawai": bad}, search_results=[bad])},  # type: ignore[dict-item]
        art={}, lyrics={}, fingerprint={}, clients=(),
    )
    t = _make_track(
        db_session,
        path="/singles/Piki - Twilight Twilight.mp3",
        duration_ms=241_000,
    )
    db_session.commit()

    result = await matching_service.propose_track_candidates(db_session, provider_set, t.id)
    assert result.candidates == ()
    assert result.rejection_reason == "insufficient identity signals" or result.rejection_reason == "candidate is not sufficiently related"


async def test_stage_group_match_creates_match_proposal_changeset(
    db_session: Session, provider_set: ProviderSet
) -> None:
    # COMPAT-CHANGESET-001 removed ChangeSet staging; stage_group_match now raises.
    t1 = _make_track(db_session, path="/b1", title="Wrong Title 1", album="Wrong Album")
    t2 = _make_track(db_session, path="/b2", title="Wrong Title 2", album="Wrong Album")
    group = TrackGroup(key="k2", album="Wrong Album")
    db_session.add(group)
    db_session.flush()
    t1.group_id = group.id
    t2.group_id = group.id
    db_session.commit()

    with pytest.raises(NotImplementedError, match="ReviewBundle"):
        await matching_service.stage_group_match(
            db_session, provider_set, group.id, source="musicbrainz", ref_id="release-1"
        )


async def test_stage_group_match_aligns_tracks_by_content_not_order(
    db_session: Session, provider_set: ProviderSet
) -> None:
    # COMPAT-CHANGESET-001: stage_group_match removed; verify it raises.
    t1 = _make_track(db_session, path="/c1", title="Svefn-g-englar", duration_ms=600_000)
    t2 = _make_track(db_session, path="/c2", title="Intro", duration_ms=100_000)
    group = TrackGroup(key="k3", album="X")
    db_session.add(group)
    db_session.flush()
    t1.group_id = group.id
    t2.group_id = group.id
    db_session.commit()

    with pytest.raises(NotImplementedError, match="ReviewBundle"):
        await matching_service.stage_group_match(
            db_session, provider_set, group.id, source="musicbrainz", ref_id="release-1"
        )


async def test_stage_group_match_unknown_provider_raises(
    db_session: Session, provider_set: ProviderSet
) -> None:
    # COMPAT-CHANGESET-001: stage_group_match always raises NotImplementedError now.
    group = TrackGroup(key="k4", album="X")
    db_session.add(group)
    db_session.flush()
    db_session.commit()

    with pytest.raises(NotImplementedError, match="ReviewBundle"):
        await matching_service.stage_group_match(
            db_session, provider_set, group.id, source="deezer", ref_id="whatever"
        )


async def test_stage_group_match_unknown_release_raises(
    db_session: Session, provider_set: ProviderSet
) -> None:
    # COMPAT-CHANGESET-001: stage_group_match always raises NotImplementedError now.
    group = TrackGroup(key="k5", album="X")
    db_session.add(group)
    db_session.flush()
    db_session.commit()

    with pytest.raises(NotImplementedError, match="ReviewBundle"):
        await matching_service.stage_group_match(
            db_session, provider_set, group.id, source="musicbrainz", ref_id="does-not-exist"
        )


async def test_stage_track_match_creates_changeset_for_singleton(
    db_session: Session, provider_set: ProviderSet
) -> None:
    # COMPAT-CHANGESET-001: stage_track_match removed; verify it raises.
    t = _make_track(db_session, path="/d1", title="Some Title", duration_ms=100_000)
    db_session.commit()

    with pytest.raises(NotImplementedError, match="ReviewBundle"):
        await matching_service.stage_track_match(
            db_session, provider_set, t.id, source="musicbrainz", ref_id="release-1"
        )
