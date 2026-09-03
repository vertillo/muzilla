from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker as sa_sessionmaker

from muzilla.audio.replaygain import TrackReplayGain
from muzilla.config.schema import Config, EnrichmentConfig, JobsConfig
from muzilla.db.models import Job, TaskAttempt, Track, WorkUnit
from muzilla.jobs import worker
from muzilla.jobs.handlers.enrich_replaygain import handle_enrich_replaygain
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate
from muzilla.providers.set import ProviderSet
from muzilla.services.proposals import ProposalComposer
from muzilla.services.reviews import get_review_bundle


def _context(*, replaygain_enabled: bool = True) -> WorkerContext:
    return WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=Config(enrichment=EnrichmentConfig(replaygain_enabled=replaygain_enabled)),
    )


@pytest.fixture(autouse=True)
def _available_replaygain_probe() -> Iterator[None]:
    with patch(
        "muzilla.jobs.handlers.enrich_replaygain.probe_replaygain_runtime",
        return_value=(True, "operational"),
    ):
        yield


def _session_factory(session: Session) -> sa_sessionmaker[Session]:
    return sa_sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)


def _make_group_with_track(session: Session, *, path: str) -> tuple[WorkUnit, Track]:
    group = WorkUnit(key=f"key-{path}", kind="album", album="Album")
    session.add(group)
    session.flush()
    track = Track(
        path=path,
        filename=Path(path).name,
        ext=".flac",
        size_bytes=1000,
        mtime_ns=1,
        title="T1",
        artist="Artist",
        work_unit_id=group.id,
    )
    session.add(track)
    session.flush()
    return group, track


def _compose_review(session: Session, group: WorkUnit, track: Track) -> int:
    candidate = ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id=f"release-{group.id}"),
        album=group.album,
        album_artist=track.artist,
        tracks=(CandidateTrack(position=1, title="Proposed title", artist=track.artist),),
    )
    return (
        ProposalComposer(session)
        .compose_candidate_for_scope(scope_type="group", scope_id=group.id, candidate=candidate)
        .id
    )


async def test_pending_replaygain_job_fails_when_disabled_after_restart(
    db_session: Session,
) -> None:
    _make_group_with_track(db_session, path="/music/a.flac")
    db_session.commit()
    job = enqueue(db_session, type="enrich_replaygain", payload={})

    await worker.run_one(
        _session_factory(db_session),
        worker_id="restart-worker",
        config=JobsConfig(job_timeout_seconds=5),
        context=_context(replaygain_enabled=False),
    )

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "failed"
    assert refreshed.error == "ReplayGain unavailable: disabled by configuration"


async def test_pending_replaygain_job_fails_when_runtime_is_unavailable_after_restart(
    db_session: Session,
) -> None:
    _make_group_with_track(db_session, path="/music/a.flac")
    db_session.commit()
    job = enqueue(db_session, type="enrich_replaygain", payload={})

    with patch(
        "muzilla.jobs.handlers.enrich_replaygain.probe_replaygain_runtime",
        return_value=(False, "rsgain executable could not start"),
    ):
        await worker.run_one(
            _session_factory(db_session),
            worker_id="restart-worker",
            config=JobsConfig(job_timeout_seconds=5),
            context=_context(),
        )

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "failed"
    assert refreshed.error == "ReplayGain unavailable: rsgain executable could not start"


async def test_handle_enrich_replaygain_adds_operations_to_existing_review(
    db_session: Session,
) -> None:
    group, track = _make_group_with_track(db_session, path="/music/a.flac")
    review_id = _compose_review(db_session, group, track)
    db_session.commit()
    job = enqueue(db_session, type="enrich_replaygain", payload={"review_bundle_id": review_id})
    results = {
        Path("/music/a.flac"): TrackReplayGain(
            path=Path("/music/a.flac"), track_gain_db=-3.2, track_peak=0.9
        ),
    }

    with patch(
        "muzilla.jobs.handlers.enrich_replaygain.compute_album_replaygain", return_value=results
    ):
        result = await handle_enrich_replaygain(
            db_session, job, ProgressReporter(db_session, job.id, coalesce_ms=0), _context()
        )

    assert (result["analyzed"], result["errored"]) == (1, 0)
    detail = get_review_bundle(db_session, review_id)
    assert detail is not None
    assert any(
        operation.kind == "set_replay_gain" for operation in detail.current_revision.operations
    )
    attempt = (
        db_session.query(TaskAttempt).filter_by(review_bundle_id=review_id, kind="replaygain").one()
    )
    assert attempt.state == "succeeded"
    refreshed = db_session.get(Track, track.id)
    assert refreshed is not None
    assert refreshed.rg_track_gain is None


async def test_partial_replaygain_failure_stays_on_the_relevant_reviews(
    db_session: Session,
) -> None:
    bad_group, bad_track = _make_group_with_track(db_session, path="/music/bad.flac")
    good_group, good_track = _make_group_with_track(db_session, path="/music/good.flac")
    bad_review_id = _compose_review(db_session, bad_group, bad_track)
    good_review_id = _compose_review(db_session, good_group, good_track)
    db_session.commit()
    job = enqueue(
        db_session,
        type="enrich_replaygain",
        payload={"review_bundle_ids": [bad_review_id, good_review_id]},
    )

    def _fake_compute(paths: list[Path]) -> dict[Path, TrackReplayGain]:
        if any(path.name == "bad.flac" for path in paths):
            raise RuntimeError("rsgain exploded")
        return {
            path: TrackReplayGain(path=path, track_gain_db=-1.0, track_peak=0.5) for path in paths
        }

    with patch(
        "muzilla.jobs.handlers.enrich_replaygain.compute_album_replaygain",
        side_effect=_fake_compute,
    ):
        result = await handle_enrich_replaygain(
            db_session, job, ProgressReporter(db_session, job.id, coalesce_ms=0), _context()
        )

    assert (result["analyzed"], result["errored"]) == (1, 1)
    attempts = db_session.query(TaskAttempt).filter(TaskAttempt.kind == "replaygain").all()
    assert {(attempt.review_bundle_id, attempt.state) for attempt in attempts} == {
        (bad_review_id, "transient_failure"),
        (good_review_id, "succeeded"),
    }


async def test_handle_enrich_replaygain_no_groups_needing_it(db_session: Session) -> None:
    job = enqueue(db_session, type="enrich_replaygain", payload={})
    result = await handle_enrich_replaygain(
        db_session, job, ProgressReporter(db_session, job.id, coalesce_ms=0), _context()
    )

    assert result == {"review_ids": [], "analyzed": 0, "errored": 0}
