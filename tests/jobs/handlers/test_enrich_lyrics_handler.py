from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import Session

from muzilla.config.schema import Config, EnrichmentConfig
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import ReviewBundle, TaskAttempt, Track
from muzilla.domain.metadata import LyricsResult
from muzilla.jobs.handlers.enrich_lyrics import handle_enrich_lyrics
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate
from muzilla.providers.errors import ProviderTransientError
from muzilla.providers.set import ProviderSet
from muzilla.services import jobs as jobs_service
from muzilla.services.proposals import ProposalComposer
from muzilla.services.reviews import apply_operation_decisions, get_review_bundle


class _StubLyricsProvider:
    async def get_lyrics(
        self, artist: str, title: str, duration_ms: int | None
    ) -> LyricsResult | None:
        return LyricsResult(text="la la la", synced=False, source="lrclib")


def _context(*, lyrics_enabled: bool = True, provider: object | None = None) -> WorkerContext:
    lyrics = (
        {"lrclib": provider or _StubLyricsProvider()}
        if provider is not None or lyrics_enabled
        else {}
    )
    return WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics=lyrics, fingerprint={}, clients=()),  # type: ignore[arg-type]
        config=Config(enrichment=EnrichmentConfig(lyrics_enabled=lyrics_enabled)),
    )


def _make_track(session: Session, *, path: str, title: str = "T1", artist: str = "A1") -> Track:
    track = Track(
        path=path,
        filename=Path(path).name,
        ext=".flac",
        size_bytes=1000,
        mtime_ns=1,
        title=title,
        artist=artist,
    )
    session.add(track)
    session.flush()
    return track


def _compose_review(session: Session, track: Track) -> int:
    candidate = ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id=f"release-{track.id}"),
        album="Album",
        album_artist=track.artist,
        tracks=(CandidateTrack(position=1, title=f"Proposed {track.title}", artist=track.artist),),
    )
    return (
        ProposalComposer(session)
        .compose_candidate_for_scope(scope_type="track", scope_id=track.id, candidate=candidate)
        .id
    )


async def test_handle_enrich_lyrics_skips_when_disabled(db_session: Session) -> None:
    _make_track(db_session, path="/music/a.flac")
    db_session.commit()
    job = enqueue(db_session, type="enrich_lyrics", payload={})

    result = await handle_enrich_lyrics(
        db_session,
        job,
        ProgressReporter(db_session, job.id, coalesce_ms=0),
        _context(lyrics_enabled=False),
    )

    assert result == {"found": 0, "not_found": 0, "errored": 0, "items": [], "skipped": True}


async def test_handle_enrich_lyrics_adds_operation_and_task_to_existing_review(
    db_session: Session,
) -> None:
    track = _make_track(db_session, path="/music/a.flac")
    review_id = _compose_review(db_session, track)
    db_session.commit()
    job = enqueue(db_session, type="enrich_lyrics", payload={"review_bundle_id": review_id})

    result = await handle_enrich_lyrics(
        db_session, job, ProgressReporter(db_session, job.id, coalesce_ms=0), _context()
    )

    assert (result["found"], result["not_found"], result["errored"]) == (1, 0, 0)
    detail = get_review_bundle(db_session, review_id)
    assert detail is not None
    assert any(operation.kind == "write_lyrics" for operation in detail.current_revision.operations)
    attempt = (
        db_session.query(TaskAttempt).filter_by(review_bundle_id=review_id, kind="lyrics").one()
    )
    assert attempt.state == "succeeded"


async def test_lyrics_completion_keeps_an_archived_inflight_review_archived(
    db_session: Session, migrated_db: Path
) -> None:
    track = _make_track(db_session, path="/music/archived-success.flac")
    review_id = _compose_review(db_session, track)
    detail = get_review_bundle(db_session, review_id)
    assert detail is not None
    revision_id = detail.current_revision.id
    rejection_decisions = tuple(
        (operation.id, "rejected") for operation in detail.current_revision.operations
    )
    db_session.commit()
    factory = create_session_factory(create_db_engine(migrated_db))

    class Provider:
        async def get_lyrics(
            self, artist: str, title: str, duration_ms: int | None
        ) -> LyricsResult | None:
            with factory() as archive_session:
                apply_operation_decisions(
                    archive_session,
                    review_id,
                    revision_id=revision_id,
                    decisions=rejection_decisions,
                )
                archive_session.commit()
                archived = get_review_bundle(archive_session, review_id)
                assert archived is not None and archived.state == "discarded"
            return LyricsResult(text="archived result", synced=False, source="lrclib")

    job = enqueue(db_session, type="enrich_lyrics", payload={"review_bundle_id": review_id})
    result = await handle_enrich_lyrics(
        db_session,
        job,
        ProgressReporter(db_session, job.id, coalesce_ms=0),
        _context(provider=Provider()),
    )

    assert result["found"] == 1, result
    assert result["review_ids"] == [review_id]
    assert db_session.query(ReviewBundle).filter_by(logical_key=f"track:{track.id}").count() == 1
    archived = get_review_bundle(db_session, review_id)
    assert archived is not None
    assert archived.state == "discarded"
    assert not any(operation.kind == "write_lyrics" for operation in archived.current_revision.operations)
    assert db_session.query(TaskAttempt).filter_by(review_bundle_id=review_id, kind="lyrics").one().state == "succeeded"


async def test_lyrics_failure_keeps_an_archived_inflight_review_archived(
    db_session: Session, migrated_db: Path
) -> None:
    track = _make_track(db_session, path="/music/archived-failure.flac")
    review_id = _compose_review(db_session, track)
    detail = get_review_bundle(db_session, review_id)
    assert detail is not None
    revision_id = detail.current_revision.id
    rejection_decisions = tuple(
        (operation.id, "rejected") for operation in detail.current_revision.operations
    )
    db_session.commit()
    factory = create_session_factory(create_db_engine(migrated_db))

    class Provider:
        async def get_lyrics(
            self, artist: str, title: str, duration_ms: int | None
        ) -> LyricsResult | None:
            with factory() as archive_session:
                apply_operation_decisions(
                    archive_session,
                    review_id,
                    revision_id=revision_id,
                    decisions=rejection_decisions,
                )
                archive_session.commit()
                archived = get_review_bundle(archive_session, review_id)
                assert archived is not None and archived.state == "discarded"
            raise ProviderTransientError("lrclib temporarily unavailable")

    job = enqueue(db_session, type="enrich_lyrics", payload={"review_bundle_id": review_id})
    result = await handle_enrich_lyrics(
        db_session,
        job,
        ProgressReporter(db_session, job.id, coalesce_ms=0),
        _context(provider=Provider()),
    )

    assert result["errored"] == 1, result
    assert result["retryable_track_ids"] == [track.id]
    assert db_session.query(ReviewBundle).filter_by(logical_key=f"track:{track.id}").count() == 1
    archived = get_review_bundle(db_session, review_id)
    assert archived is not None and archived.state == "discarded"
    assert db_session.query(TaskAttempt).filter_by(review_bundle_id=review_id, kind="lyrics").one().state == "transient_failure"

    reopened = apply_operation_decisions(
        db_session,
        review_id,
        revision_id=archived.current_revision.id,
        decisions=((archived.current_revision.operations[0].id, "accepted"),),
    )

    assert reopened.state == "needs_attention"


async def test_handle_enrich_lyrics_records_not_found_on_same_review(db_session: Session) -> None:
    track = _make_track(db_session, path="/music/a.flac")
    review_id = _compose_review(db_session, track)
    db_session.commit()
    job = enqueue(db_session, type="enrich_lyrics", payload={"review_bundle_id": review_id})

    with patch.object(_StubLyricsProvider, "get_lyrics", return_value=None):
        result = await handle_enrich_lyrics(
            db_session, job, ProgressReporter(db_session, job.id, coalesce_ms=0), _context()
        )

    assert result["items"] == [{"track_id": track.id, "outcome": "not_found", "retryable": False}]
    attempt = (
        db_session.query(TaskAttempt).filter_by(review_bundle_id=review_id, kind="lyrics").one()
    )
    assert attempt.state == "not_found"


async def test_partial_lyrics_failure_is_retryable_on_the_same_review(db_session: Session) -> None:
    missing_track = _make_track(db_session, path="/music/missing.flac", title="Missing")
    failed_track = _make_track(db_session, path="/music/failed.flac", title="Failed")
    missing_review_id = _compose_review(db_session, missing_track)
    failed_review_id = _compose_review(db_session, failed_track)
    db_session.commit()

    class Provider:
        async def get_lyrics(
            self, artist: str, title: str, duration_ms: int | None
        ) -> LyricsResult | None:
            if title == "Missing":
                return None
            raise ProviderTransientError("lrclib temporarily unavailable")

    job = enqueue(
        db_session,
        type="enrich_lyrics",
        payload={"review_bundle_ids": [missing_review_id, failed_review_id]},
    )
    result = await handle_enrich_lyrics(
        db_session,
        job,
        ProgressReporter(db_session, job.id, coalesce_ms=0),
        _context(provider=Provider()),
    )

    assert result["retryable_track_ids"] == [failed_track.id]
    attempts = db_session.query(TaskAttempt).filter(TaskAttempt.kind == "lyrics").all()
    assert {(attempt.review_bundle_id, attempt.state) for attempt in attempts} == {
        (missing_review_id, "not_found"),
        (failed_review_id, "transient_failure"),
    }

    retry = jobs_service.retry_review_task(db_session, failed_review_id, kind="lyrics")
    retry_attempt = (
        db_session.query(TaskAttempt)
        .filter_by(review_bundle_id=failed_review_id, kind="lyrics", attempt_no=2)
        .one()
    )
    assert retry.type == "enrich_lyrics"
    assert retry_attempt.state == "pending"
