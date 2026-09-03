from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import Session

from muzilla.audio.art import ProcessedArt
from muzilla.config.schema import Config, EnrichmentConfig, StorageConfig
from muzilla.db.models import TaskAttempt, Track, WorkUnit
from muzilla.jobs.handlers.enrich_art import handle_enrich_art
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.base import ArtRef, CandidateTrack, ProviderRef, ReleaseCandidate
from muzilla.providers.set import ProviderSet
from muzilla.services.proposals import ProposalComposer
from muzilla.services.reviews import get_review_bundle


class _StubArtProvider:
    async def get_art(self, ref: object) -> list[ArtRef]:
        return [ArtRef(url="https://example.invalid/cover.jpg", source="coverartarchive")]


def _context(tmp_path: Path, *, with_art_provider: bool = True) -> WorkerContext:
    art = {"coverartarchive": _StubArtProvider()} if with_art_provider else {}
    return WorkerContext(
        provider_set=ProviderSet(metadata={}, art=art, lyrics={}, fingerprint={}, clients=()),  # type: ignore[arg-type]
        config=Config(
            enrichment=EnrichmentConfig(),
            storage=StorageConfig(blob_dir=tmp_path / "blobs"),
        ),
    )


def _make_group_with_track(
    session: Session, *, path: str, mb_release_id: str = "rel-1"
) -> tuple[WorkUnit, Track]:
    group = WorkUnit(key=f"key-{path}", kind="album", album="Album", mb_release_id=mb_release_id)
    session.add(group)
    session.flush()
    track = Track(
        path=path,
        filename=Path(path).name,
        ext=".flac",
        size_bytes=1000,
        mtime_ns=1,
        title="T1",
        work_unit_id=group.id,
    )
    session.add(track)
    session.flush()
    return group, track


def _compose_review(session: Session, group: WorkUnit, track: Track) -> int:
    candidate = ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id=group.mb_release_id or "rel-1"),
        album=group.album or "Album",
        album_artist="Artist",
        mb_release_id=group.mb_release_id,
        tracks=(CandidateTrack(position=1, title="Proposed title", artist="Artist"),),
    )
    return (
        ProposalComposer(session)
        .compose_candidate_for_scope(scope_type="group", scope_id=group.id, candidate=candidate)
        .id
    )


async def test_handle_enrich_art_skips_when_provider_not_configured(
    db_session: Session, tmp_path: Path
) -> None:
    _make_group_with_track(db_session, path="/music/a.flac")
    db_session.commit()

    job = enqueue(db_session, type="enrich_art", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_enrich_art(
        db_session, job, progress, _context(tmp_path, with_art_provider=False)
    )

    assert result == {"embedded": 0, "not_found": 0, "errored": 0, "skipped": True}


async def test_handle_enrich_art_adds_candidate_and_task_to_existing_review(
    db_session: Session, tmp_path: Path
) -> None:
    group, track = _make_group_with_track(db_session, path="/music/a.flac")
    review_id = _compose_review(db_session, group, track)
    db_session.commit()

    job = enqueue(db_session, type="enrich_art", payload={"review_bundle_id": review_id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    with patch(
        "muzilla.jobs.handlers.enrich_art.fetch_and_process_art",
        return_value=ProcessedArt(b"jpeg bytes", "image/jpeg", 600, 600),
    ):
        result = await handle_enrich_art(db_session, job, progress, _context(tmp_path))

    assert result["embedded"] == 1
    assert result["not_found"] == 0
    assert result["errored"] == 0
    db_session.expire_all()
    detail = get_review_bundle(db_session, review_id)
    assert detail is not None
    assert len(detail.cover_candidates) == 1
    assert any(operation.kind == "embed_art" for operation in detail.current_revision.operations)
    attempt = (
        db_session.query(TaskAttempt).filter_by(review_bundle_id=review_id, kind="cover").one()
    )
    assert attempt.state == "succeeded"
    refreshed_group = db_session.get(WorkUnit, group.id)
    assert refreshed_group is not None
    assert refreshed_group.art_blob_id is None  # proposal is not current catalog state
    refreshed_track = db_session.get(Track, track.id)
    assert refreshed_track is not None
    assert refreshed_track.has_embedded_art is False  # DRAFT until applied


async def test_handle_enrich_art_counts_not_found(db_session: Session, tmp_path: Path) -> None:
    group, track = _make_group_with_track(db_session, path="/music/a.flac")
    review_id = _compose_review(db_session, group, track)
    db_session.commit()

    job = enqueue(db_session, type="enrich_art", payload={"review_bundle_id": review_id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    with patch("muzilla.jobs.handlers.enrich_art.fetch_and_process_art", return_value=None):
        result = await handle_enrich_art(db_session, job, progress, _context(tmp_path))

    assert result == {"review_ids": [], "embedded": 0, "not_found": 1, "errored": 0}
    attempt = (
        db_session.query(TaskAttempt).filter_by(review_bundle_id=review_id, kind="cover").one()
    )
    assert attempt.state == "not_found"


async def test_handle_enrich_art_continues_past_a_failing_group(
    db_session: Session, tmp_path: Path
) -> None:
    bad_group, bad_track = _make_group_with_track(
        db_session, path="/music/bad.flac", mb_release_id="rel-bad"
    )
    good_group, good_track = _make_group_with_track(
        db_session, path="/music/good.flac", mb_release_id="rel-good"
    )
    bad_review_id = _compose_review(db_session, bad_group, bad_track)
    good_review_id = _compose_review(db_session, good_group, good_track)
    db_session.commit()

    job = enqueue(
        db_session,
        type="enrich_art",
        payload={"review_bundle_ids": [bad_review_id, good_review_id]},
    )
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    def _fake_fetch(
        client: object, provider: object, mb_release_id: str, **kwargs: object
    ) -> object:
        if mb_release_id == "rel-bad":
            raise RuntimeError("network exploded")
        return ProcessedArt(b"jpeg bytes", "image/jpeg", 600, 600)

    with patch("muzilla.jobs.handlers.enrich_art.fetch_and_process_art", side_effect=_fake_fetch):
        result = await handle_enrich_art(db_session, job, progress, _context(tmp_path))

    assert result["errored"] == 1
    assert result["embedded"] == 1
    db_session.expire_all()
    attempts = db_session.query(TaskAttempt).filter(TaskAttempt.kind == "cover").all()
    assert {(attempt.review_bundle_id, attempt.state) for attempt in attempts} == {
        (bad_review_id, "transient_failure"),
        (good_review_id, "succeeded"),
    }
