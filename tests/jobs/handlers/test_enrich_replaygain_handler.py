from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker as sa_sessionmaker

from muzilla.audio.replaygain import TrackReplayGain
from muzilla.config.schema import Config, EnrichmentConfig, JobsConfig
from muzilla.db.models import ChangeSet, Job, Track, TrackGroup
from muzilla.jobs import worker
from muzilla.jobs.handlers.enrich_replaygain import handle_enrich_replaygain
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.set import ProviderSet


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


def _make_group_with_track(session: Session, *, path: str) -> tuple[TrackGroup, Track]:
    group = TrackGroup(key=f"key-{path}", kind="album", album="Album")
    session.add(group)
    session.flush()
    track = Track(
        path=path, filename=Path(path).name, ext=".flac", size_bytes=1000, mtime_ns=1,
        title="T1", group_id=group.id,
    )
    session.add(track)
    session.flush()
    return group, track


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
    assert refreshed.result is None
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
    assert refreshed.result is None
    assert refreshed.error == "ReplayGain unavailable: rsgain executable could not start"


async def test_handle_enrich_replaygain_stages_changeset_per_group(db_session: Session) -> None:
    group, track = _make_group_with_track(db_session, path="/music/a.flac")
    db_session.commit()

    job = enqueue(db_session, type="enrich_replaygain", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    results = {
        Path("/music/a.flac"): TrackReplayGain(
            path=Path("/music/a.flac"), track_gain_db=-3.2, track_peak=0.9
        ),
    }
    with patch("muzilla.pipeline.enrichment.compute_album_replaygain", return_value=results):
        result = await handle_enrich_replaygain(db_session, job, progress, _context())

    assert result["analyzed"] == 1
    assert result["errored"] == 0
    change_set_ids = result["change_set_ids"]
    assert isinstance(change_set_ids, list)
    assert len(change_set_ids) == 1

    db_session.expire_all()
    change_set = db_session.get(ChangeSet, change_set_ids[0])
    assert change_set is not None
    assert change_set.scope_id == group.id
    refreshed = db_session.get(Track, track.id)
    assert refreshed is not None
    # enrichment ChangeSets auto-accept but stay DRAFT until applied —
    # the field itself isn't written until the changeset is applied.
    assert refreshed.rg_track_gain is None


async def test_handle_enrich_replaygain_continues_past_a_failing_group(db_session: Session) -> None:
    _make_group_with_track(db_session, path="/music/bad.flac")
    good_group, _ = _make_group_with_track(db_session, path="/music/good.flac")
    db_session.commit()

    job = enqueue(db_session, type="enrich_replaygain", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    def _fake_compute(paths: list[Path]) -> dict[Path, TrackReplayGain]:
        if any(p.name == "bad.flac" for p in paths):
            raise RuntimeError("rsgain exploded")
        return {p: TrackReplayGain(path=p, track_gain_db=-1.0, track_peak=0.5) for p in paths}

    with patch("muzilla.pipeline.enrichment.compute_album_replaygain", side_effect=_fake_compute):
        result = await handle_enrich_replaygain(db_session, job, progress, _context())

    assert result["errored"] == 1
    assert result["analyzed"] == 1
    change_set_ids = result["change_set_ids"]
    assert isinstance(change_set_ids, list)
    db_session.expire_all()
    change_set = db_session.get(ChangeSet, change_set_ids[0])
    assert change_set is not None
    assert change_set.scope_id == good_group.id


async def test_replaygain_job_fails_when_every_group_errors(db_session: Session) -> None:
    _make_group_with_track(db_session, path="/music/bad.flac")
    db_session.commit()
    job = enqueue(db_session, type="enrich_replaygain", payload={})

    with patch(
        "muzilla.pipeline.enrichment.compute_album_replaygain",
        side_effect=RuntimeError("rsgain exploded"),
    ):
        await worker.run_one(
            _session_factory(db_session),
            worker_id="failure-worker",
            config=JobsConfig(job_timeout_seconds=5),
            context=_context(),
        )

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "failed"
    assert refreshed.result is None
    assert refreshed.error == "ReplayGain failed for all 1 group(s)"


async def test_handle_enrich_replaygain_no_groups_needing_it(db_session: Session) -> None:
    job = enqueue(db_session, type="enrich_replaygain", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_enrich_replaygain(db_session, job, progress, _context())

    assert result == {"change_set_ids": [], "analyzed": 0, "errored": 0}
