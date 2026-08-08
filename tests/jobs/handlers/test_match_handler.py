from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlalchemy.orm import Session

from muzilla.config.schema import Config, EnrichmentConfig
from muzilla.db.models import ReviewBundle, TaskAttempt, Track, TrackGroup
from muzilla.jobs.handlers import match as match_handler
from muzilla.jobs.handlers.match import handle_match
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.matching import CandidateRow, TrackMatchProposal
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate, ReleaseQuery
from muzilla.providers.set import ProviderSet
from muzilla.services.reviews import get_review_bundle


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


def _context(*, metadata_auto: bool = True) -> WorkerContext:
    stub = StubProvider(releases={"release-1": _release()}, search_results=[_release()])
    return WorkerContext(
        provider_set=ProviderSet(
            metadata={"musicbrainz": stub},  # type: ignore[dict-item]
            art={},
            lyrics={},
            fingerprint={},
            clients=(),
        ),
        config=Config(enrichment=EnrichmentConfig(metadata_auto=metadata_auto)),
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


async def test_handle_match_composes_album_review_bundle(db_session: Session) -> None:
    t1 = _make_track(
        db_session,
        path="/a1",
        title="Intro",
        album="Agaetis byrjun",
        album_artist="Sigur Ros",
        duration_ms=100_000,
    )
    t2 = _make_track(
        db_session,
        path="/a2",
        title="Svefn-g-englar",
        album="Agaetis byrjun",
        album_artist="Sigur Ros",
        duration_ms=600_000,
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
    review = db_session.query(ReviewBundle).one()
    assert review.scope_type == "group"
    assert review.scope_id == group.id
    detail = get_review_bundle(db_session, review.id)
    assert detail is not None
    assert detail.current_revision.candidate_source == "musicbrainz"
    attempts = db_session.query(TaskAttempt).filter(TaskAttempt.review_bundle_id == review.id).all()
    assert sorted((attempt.kind, attempt.item_key, attempt.state) for attempt in attempts) == [
        ("cover", f"group:{group.id}", "pending"),
        ("lyrics", f"track:{t1.id}", "pending"),
        ("lyrics", f"track:{t2.id}", "pending"),
        ("replaygain", f"group:{group.id}", "pending"),
    ]


async def test_handle_match_composes_singleton_review_bundle(db_session: Session) -> None:
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
    review = db_session.query(ReviewBundle).one()
    assert review.scope_type == "track"
    assert review.scope_id == t.id


async def test_match_exposes_pending_optional_sections_before_workers_start(
    db_session: Session,
) -> None:
    track = _make_track(db_session, path="/pending", title="Intro", duration_ms=100_000)
    group = TrackGroup(key="pending-singleton", kind="singleton")
    db_session.add(group)
    db_session.flush()
    track.group_id = group.id
    db_session.commit()
    job = enqueue(db_session, type="match", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_match(db_session, job, progress, _context())

    review = db_session.query(ReviewBundle).one()
    attempts = list(
        db_session.query(TaskAttempt)
        .filter(TaskAttempt.review_bundle_id == review.id)
        .order_by(TaskAttempt.kind)
    )
    assert result["proposed"] == 1
    assert [(attempt.kind, attempt.state) for attempt in attempts] == [
        ("cover", "pending"),
        ("lyrics", "pending"),
        ("replaygain", "pending"),
    ]
    assert all(attempt.job_id is not None for attempt in attempts)


async def test_handle_match_skips_automatic_metadata_when_disabled(db_session: Session) -> None:
    t = _make_track(db_session, path="/s2", title="Intro", duration_ms=100_000)
    group = TrackGroup(key="k3", kind="singleton")
    db_session.add(group)
    db_session.flush()
    t.group_id = group.id
    db_session.commit()

    job = enqueue(db_session, type="match", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_match(db_session, job, progress, _context(metadata_auto=False))

    assert result == {
        "proposed": 0,
        "skipped_no_candidates": 0,
        "enrichment_job_ids": [],
        "skipped": True,
    }
    assert db_session.query(ReviewBundle).count() == 0


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
        candidate_type="release",
        representative_title="Track",
        representative_artist="Artist",
        representative_position=1,
        representative_duration_ms=100_000,
        cover_url=None,
        distance=0.0,
        adjusted_distance=0.0,
        score_signals=(),
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

    def compose_must_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("a cancelled fetch must not compose a ReviewBundle")

    monkeypatch.setattr(match_handler, "propose_track_candidates", cancelled_proposal)
    monkeypatch.setattr(
        match_handler.ProposalComposer, "compose_candidate_for_scope", compose_must_not_run
    )

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
    assert db_session.query(ReviewBundle).count() == 0
