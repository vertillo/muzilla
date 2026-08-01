from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import Session

from muzilla.config.schema import Config, EnrichmentConfig
from muzilla.db.models import ChangeSet, Track
from muzilla.domain.metadata import LyricsResult
from muzilla.jobs.handlers.enrich_lyrics import handle_enrich_lyrics
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.errors import ProviderTransientError
from muzilla.providers.set import ProviderSet


class _StubLyricsProvider:
    async def get_lyrics(self, artist: str, title: str, duration_ms: int | None) -> LyricsResult | None:
        return LyricsResult(text="la la la", synced=False, source="lrclib")


def _context(*, lyrics_enabled: bool = True, with_provider: bool = True) -> WorkerContext:
    lyrics = {"lrclib": _StubLyricsProvider()} if with_provider else {}
    return WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics=lyrics, fingerprint={}, clients=()),  # type: ignore[arg-type]
        config=Config(enrichment=EnrichmentConfig(lyrics_enabled=lyrics_enabled)),
    )


def _make_track(session: Session, *, path: str, title: str = "T1", artist: str = "A1") -> Track:
    t = Track(
        path=path, filename=Path(path).name, ext=".flac", size_bytes=1000, mtime_ns=1,
        title=title, artist=artist,
    )
    session.add(t)
    session.flush()
    return t


async def test_handle_enrich_lyrics_skips_when_disabled(db_session: Session) -> None:
    _make_track(db_session, path="/music/a.flac")
    db_session.commit()

    job = enqueue(db_session, type="enrich_lyrics", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_enrich_lyrics(db_session, job, progress, _context(lyrics_enabled=False))

    assert result == {"found": 0, "not_found": 0, "errored": 0, "items": [], "skipped": True}


async def test_handle_enrich_lyrics_skips_when_provider_not_configured(db_session: Session) -> None:
    _make_track(db_session, path="/music/a.flac")
    db_session.commit()

    job = enqueue(db_session, type="enrich_lyrics", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_enrich_lyrics(db_session, job, progress, _context(with_provider=False))

    assert result == {"found": 0, "not_found": 0, "errored": 0, "items": [], "skipped": True}


async def test_handle_enrich_lyrics_stages_changeset(db_session: Session) -> None:
    track = _make_track(db_session, path="/music/a.flac")
    db_session.commit()

    job = enqueue(db_session, type="enrich_lyrics", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_enrich_lyrics(db_session, job, progress, _context())

    assert result["found"] == 1
    assert result["not_found"] == 0
    assert result["errored"] == 0
    change_set_ids = result["change_set_ids"]
    assert isinstance(change_set_ids, list)
    assert len(change_set_ids) == 1

    db_session.expire_all()
    change_set = db_session.get(ChangeSet, change_set_ids[0])
    assert change_set is not None
    assert change_set.scope_id == track.id


async def test_handle_enrich_lyrics_counts_not_found(db_session: Session) -> None:
    _make_track(db_session, path="/music/a.flac")
    db_session.commit()

    job = enqueue(db_session, type="enrich_lyrics", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    with patch(
        "muzilla.jobs.handlers.enrich_lyrics.stage_lyrics_for_track", return_value=None
    ):
        result = await handle_enrich_lyrics(db_session, job, progress, _context())

    assert result == {
        "change_set_ids": [],
        "found": 0,
        "not_found": 1,
        "errored": 0,
        "retryable_track_ids": [],
        "items": [{"track_id": 1, "outcome": "not_found", "retryable": False}],
    }


async def test_handle_enrich_lyrics_continues_past_a_failing_track(db_session: Session) -> None:
    _make_track(db_session, path="/music/bad.flac", title="Bad")
    good_track = _make_track(db_session, path="/music/good.flac", title="Good")
    db_session.commit()

    job = enqueue(db_session, type="enrich_lyrics", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    async def _fake_stage(session: object, track: Track, provider: object) -> object:
        if track.title == "Bad":
            raise RuntimeError("network exploded")
        from muzilla.changes.builder import FieldEdit, build_changeset

        return build_changeset(
            session,  # type: ignore[arg-type]
            title="Lyrics",
            source="enrichment",
            edits={track.id: [FieldEdit(field="lyrics", new_value={"text": "x", "synced": False}, op="write_lyrics")]},
            entity_type="track",
            scope_type="track",
            scope_id=track.id,
            created_by="job",
        )

    with patch("muzilla.jobs.handlers.enrich_lyrics.stage_lyrics_for_track", side_effect=_fake_stage):
        result = await handle_enrich_lyrics(db_session, job, progress, _context())

    assert result["errored"] == 1
    assert result["found"] == 1
    change_set_ids = result["change_set_ids"]
    assert isinstance(change_set_ids, list)
    db_session.expire_all()
    change_set = db_session.get(ChangeSet, change_set_ids[0])
    assert change_set is not None
    assert change_set.scope_id == good_track.id


async def test_handle_enrich_lyrics_persists_per_item_not_found_and_retryable_error(
    db_session: Session,
) -> None:
    missing_track = _make_track(db_session, path="/music/missing.flac", title="Missing")
    failed_track = _make_track(db_session, path="/music/failed.flac", title="Failed")
    db_session.commit()

    class Provider:
        async def get_lyrics(
            self, artist: str, title: str, duration_ms: int | None
        ) -> LyricsResult | None:
            if title == "Missing":
                return None
            raise ProviderTransientError("lrclib temporarily unavailable")

    context = WorkerContext(
        provider_set=ProviderSet(
            metadata={}, art={}, lyrics={"lrclib": Provider()}, fingerprint={}, clients=()
        ),  # type: ignore[arg-type]
        config=Config(),
    )
    job = enqueue(db_session, type="enrich_lyrics", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_enrich_lyrics(db_session, job, progress, context)

    assert result["not_found"] == 1
    assert result["errored"] == 1
    assert result["retryable_track_ids"] == [failed_track.id]
    assert result["items"] == [
        {"track_id": missing_track.id, "outcome": "not_found", "retryable": False},
        {
            "track_id": failed_track.id,
            "outcome": "transient_error",
            "retryable": True,
            "error": "lrclib temporarily unavailable",
        },
    ]
