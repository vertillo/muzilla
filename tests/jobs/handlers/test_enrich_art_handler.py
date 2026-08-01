from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import Session

from muzilla.config.schema import Config, EnrichmentConfig, StorageConfig
from muzilla.db.models import ChangeSet, Track, TrackGroup
from muzilla.jobs.handlers.enrich_art import handle_enrich_art
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.base import ArtRef
from muzilla.providers.set import ProviderSet


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


def _make_group_with_track(session: Session, *, path: str, mb_release_id: str = "rel-1") -> tuple[TrackGroup, Track]:
    group = TrackGroup(key=f"key-{path}", kind="album", album="Album", mb_release_id=mb_release_id)
    session.add(group)
    session.flush()
    track = Track(
        path=path, filename=Path(path).name, ext=".flac", size_bytes=1000, mtime_ns=1,
        title="T1", group_id=group.id,
    )
    session.add(track)
    session.flush()
    return group, track


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


async def test_handle_enrich_art_embeds_and_stages_changeset(db_session: Session, tmp_path: Path) -> None:
    group, track = _make_group_with_track(db_session, path="/music/a.flac")
    db_session.commit()

    job = enqueue(db_session, type="enrich_art", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    with patch(
        "muzilla.jobs.handlers.enrich_art.fetch_and_process_art",
        return_value=(b"jpeg bytes", "image/jpeg"),
    ):
        result = await handle_enrich_art(db_session, job, progress, _context(tmp_path))

    assert result["embedded"] == 1
    assert result["not_found"] == 0
    assert result["errored"] == 0
    change_set_ids = result["change_set_ids"]
    assert isinstance(change_set_ids, list)
    assert len(change_set_ids) == 1

    db_session.expire_all()
    change_set = db_session.get(ChangeSet, change_set_ids[0])
    assert change_set is not None
    assert change_set.scope_id == group.id
    refreshed_group = db_session.get(TrackGroup, group.id)
    assert refreshed_group is not None
    assert refreshed_group.art_blob_id is None  # proposal is not current catalog state
    refreshed_track = db_session.get(Track, track.id)
    assert refreshed_track is not None
    assert refreshed_track.has_embedded_art is False  # DRAFT until applied


async def test_handle_enrich_art_counts_not_found(db_session: Session, tmp_path: Path) -> None:
    _make_group_with_track(db_session, path="/music/a.flac")
    db_session.commit()

    job = enqueue(db_session, type="enrich_art", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    with patch("muzilla.jobs.handlers.enrich_art.fetch_and_process_art", return_value=None):
        result = await handle_enrich_art(db_session, job, progress, _context(tmp_path))

    assert result == {"change_set_ids": [], "embedded": 0, "not_found": 1, "errored": 0}


async def test_handle_enrich_art_continues_past_a_failing_group(db_session: Session, tmp_path: Path) -> None:
    _make_group_with_track(db_session, path="/music/bad.flac", mb_release_id="rel-bad")
    good_group, _ = _make_group_with_track(db_session, path="/music/good.flac", mb_release_id="rel-good")
    db_session.commit()

    job = enqueue(db_session, type="enrich_art", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    def _fake_fetch(client: object, provider: object, mb_release_id: str, **kwargs: object) -> object:
        if mb_release_id == "rel-bad":
            raise RuntimeError("network exploded")
        return (b"jpeg bytes", "image/jpeg")

    with patch("muzilla.jobs.handlers.enrich_art.fetch_and_process_art", side_effect=_fake_fetch):
        result = await handle_enrich_art(db_session, job, progress, _context(tmp_path))

    assert result["errored"] == 1
    assert result["embedded"] == 1
    change_set_ids = result["change_set_ids"]
    assert isinstance(change_set_ids, list)
    db_session.expire_all()
    change_set = db_session.get(ChangeSet, change_set_ids[0])
    assert change_set is not None
    assert change_set.scope_id == good_group.id
