from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from muzilla.changes import writer as writer_module
from muzilla.config.schema import Config, EnrichmentConfig, StorageConfig
from muzilla.db.models import ProposalRevision, ReviewBundle, TaskAttempt, Track, WorkUnit
from muzilla.domain.metadata import tag_hash
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
from muzilla.tags.reader import read_track

FIXTURE_AUDIO = Path(__file__).parents[2] / "fixtures/audio/silence.mp3"


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


def _context(*, metadata_auto: bool = True, library_root: Path | None = None) -> WorkerContext:
    stub = StubProvider(releases={"release-1": _release()}, search_results=[_release()])
    return WorkerContext(
        provider_set=ProviderSet(
            metadata={"musicbrainz": stub},  # type: ignore[dict-item]
            art={},
            lyrics={},
            fingerprint={},
            clients=(),
        ),
        config=Config(
            enrichment=EnrichmentConfig(metadata_auto=metadata_auto),
            storage=(
                StorageConfig(library_root=library_root)
                if library_root is not None
                else StorageConfig()
            ),
        ),
    )


def _write_fixture(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE_AUDIO, path)
    return path


def _make_track(session: Session, *, path: str, **kwargs: object) -> Track:
    source = Path(path)
    stat = source.stat() if source.is_file() else None
    metadata_hash = tag_hash(read_track(source)) if stat is not None else None
    t = Track(
        path=path,
        filename=source.name,
        ext=".mp3",
        size_bytes=kwargs.pop("size_bytes", stat.st_size if stat is not None else 1000),
        mtime_ns=kwargs.pop("mtime_ns", stat.st_mtime_ns if stat is not None else 1),
        tag_hash=kwargs.pop("tag_hash", metadata_hash),
        **kwargs,
    )
    session.add(t)
    session.flush()
    return t


async def test_handle_match_composes_album_review_bundle(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    event_loop_thread = threading.get_ident()
    capture_threads: list[int] = []
    original_capture = writer_module.capture_file_guard

    def capture_source(path: Path, root: Path | None) -> dict[str, object]:
        capture_threads.append(threading.get_ident())
        return original_capture(path, root)

    monkeypatch.setattr(writer_module, "capture_file_guard", capture_source)
    t1 = _make_track(
        db_session,
        path=str(_write_fixture(library / "a1.mp3")),
        title="Intro",
        album="Agaetis byrjun",
        album_artist="Sigur Ros",
        duration_ms=100_000,
    )
    t2 = _make_track(
        db_session,
        path=str(_write_fixture(library / "a2.mp3")),
        title="Svefn-g-englar",
        album="Agaetis byrjun",
        album_artist="Sigur Ros",
        duration_ms=600_000,
    )
    group = WorkUnit(key="k1", kind="album", album="Agaetis byrjun", album_artist="Sigur Ros")
    db_session.add(group)
    db_session.flush()
    t1.work_unit_id = group.id
    t2.work_unit_id = group.id
    db_session.commit()

    job = enqueue(db_session, type="match", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_match(db_session, job, progress, _context(library_root=library))

    assert result["proposed"] == 1
    db_session.expire_all()
    refreshed_group = db_session.get(WorkUnit, group.id)
    assert refreshed_group is not None
    assert refreshed_group.match_state == "proposed"
    review = db_session.query(ReviewBundle).one()
    assert review.scope_type == "group"
    assert review.scope_id == group.id
    detail = get_review_bundle(db_session, review.id)
    assert detail is not None
    assert detail.current_revision.candidate_source == "musicbrainz"
    revision = (
        db_session.query(ProposalRevision)
        .filter_by(review_bundle_id=review.id, is_current=True)
        .one()
    )
    snapshot_items = revision.source_snapshot.payload.get("items")
    assert isinstance(snapshot_items, list)
    assert {item.get("source_id") for item in snapshot_items if isinstance(item, dict)} == {
        t1.id,
        t2.id,
    }
    assert all(
        isinstance(item.get("physical_guard"), dict)
        for item in snapshot_items
        if isinstance(item, dict)
    )
    assert len(capture_threads) == 2
    assert all(thread_id != event_loop_thread for thread_id in capture_threads)
    attempts = db_session.query(TaskAttempt).filter(TaskAttempt.review_bundle_id == review.id).all()
    assert sorted((attempt.kind, attempt.item_key, attempt.state) for attempt in attempts) == [
        ("cover", f"group:{group.id}", "pending"),
        ("lyrics", f"track:{t1.id}", "pending"),
        ("lyrics", f"track:{t2.id}", "pending"),
        ("replaygain", f"group:{group.id}", "pending"),
    ]


@pytest.mark.parametrize("with_placeholder", [False, True], ids=["new-review", "prepared-review"])
async def test_handle_match_composes_singleton_review_bundle(
    db_session: Session, tmp_path: Path, with_placeholder: bool
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    t = _make_track(
        db_session,
        path=str(_write_fixture(library / "s1.mp3")),
        title="Intro",
        duration_ms=100_000,
    )
    group = WorkUnit(key="k2", kind="singleton")
    db_session.add(group)
    db_session.flush()
    t.work_unit_id = group.id
    placeholder_id: int | None = None
    if with_placeholder:
        from muzilla.pipeline.reviews import put_revision

        placeholder = put_revision(
            db_session,
            logical_key=f"track:{t.id}",
            title=f"Review {t.filename}",
            scope_type="track",
            scope_id=t.id,
            source_snapshot={
                "items": [
                    {
                        "source_type": "track",
                        "source_id": t.id,
                        "path": t.path,
                        "filename": t.filename,
                        "size_bytes": t.size_bytes,
                        "mtime_ns": t.mtime_ns,
                        "content_hash": t.content_hash,
                        "tag_hash": t.tag_hash,
                        "art_blob_id": t.art_blob_id,
                    }
                ]
            },
            operations=(),
        )
        placeholder_id = placeholder.bundle_id
    db_session.commit()

    job = enqueue(db_session, type="match", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_match(db_session, job, progress, _context(library_root=library))

    assert result["proposed"] == 1
    review = db_session.query(ReviewBundle).one()
    assert review.scope_type == "track"
    assert review.scope_id == t.id
    if with_placeholder:
        assert placeholder_id is not None and review.id == placeholder_id
    else:
        assert placeholder_id is None
    detail = get_review_bundle(db_session, review.id)
    assert detail is not None
    assert detail.current_revision.revision_no == (2 if with_placeholder else 1)
    revision = (
        db_session.query(ProposalRevision)
        .filter_by(review_bundle_id=review.id, is_current=True)
        .one()
    )
    items = revision.source_snapshot.payload.get("items")
    assert isinstance(items, list) and len(items) == 1
    assert isinstance(items[0], dict)
    assert isinstance(items[0].get("physical_guard"), dict)


async def test_match_exposes_pending_optional_sections_before_workers_start(
    db_session: Session, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    track = _make_track(
        db_session,
        path=str(_write_fixture(library / "pending.mp3")),
        title="Intro",
        duration_ms=100_000,
    )
    group = WorkUnit(key="pending-singleton", kind="singleton")
    db_session.add(group)
    db_session.flush()
    track.work_unit_id = group.id
    db_session.commit()
    job = enqueue(db_session, type="match", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_match(db_session, job, progress, _context(library_root=library))

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
    group = WorkUnit(key="k3", kind="singleton")
    db_session.add(group)
    db_session.flush()
    t.work_unit_id = group.id
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
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    track = _make_track(
        db_session,
        path=str(_write_fixture(library / "cancelled.mp3")),
        title="Intro",
        duration_ms=100_000,
    )
    group = WorkUnit(key="cancelled-singleton", kind="singleton")
    db_session.add(group)
    db_session.flush()
    track.work_unit_id = group.id
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
        match_handler.ProposalComposer,  # type: ignore[attr-defined]
        "compose_candidate_for_scope",
        compose_must_not_run,
    )

    with pytest.raises(JobCancelled) as exc_info:
        await handle_match(db_session, job, progress, _context(library_root=library))

    assert exc_info.value.result == {
        "proposed": 0,
        "skipped_no_candidates": 0,
        "partial": True,
    }
    db_session.expire_all()
    refreshed_group = db_session.get(WorkUnit, group.id)
    assert refreshed_group is not None
    assert refreshed_group.match_state == "unmatched"
    assert db_session.query(ReviewBundle).count() == 0


async def test_scoped_match_skips_mixed_group_and_proposes_fully_scoped_only(
    db_session: Session, tmp_path: Path
) -> None:
    """Mixed groups (in-scope + out-of-scope) must not create/mutate reviews."""
    # Fully scoped album group (both tracks inside subA) must be proposed.
    library = tmp_path / "library"
    sub_a, sub_b = library / "subA", library / "subB"
    t_in1 = _make_track(
        db_session,
        path=str(_write_fixture(sub_a / "a1.mp3")),
        title="Intro",
        album="Agaetis byrjun",
        album_artist="Sigur Ros",
        duration_ms=100_000,
    )
    t_in2 = _make_track(
        db_session,
        path=str(_write_fixture(sub_a / "a2.mp3")),
        title="Svefn-g-englar",
        album="Agaetis byrjun",
        album_artist="Sigur Ros",
        duration_ms=600_000,
    )
    group_scoped = WorkUnit(
        key="scoped-album", kind="album", album="Agaetis byrjun", album_artist="Sigur Ros"
    )
    db_session.add(group_scoped)
    db_session.flush()
    t_in1.work_unit_id = group_scoped.id
    t_in2.work_unit_id = group_scoped.id
    # Mixed pre-existing group: one in-scope, one out-of-scope. Must not be matched.
    t_mixed_in = _make_track(
        db_session,
        path=str(_write_fixture(sub_a / "mixed_in.mp3")),
        title="Intro",
        album="Agaetis byrjun",
        album_artist="Sigur Ros",
        duration_ms=100_000,
    )
    t_mixed_out = _make_track(
        db_session,
        path=str(_write_fixture(sub_b / "mixed_out.mp3")),
        title="Intro",
        album="Agaetis byrjun",
        album_artist="Sigur Ros",
        duration_ms=100_000,
    )
    group_mixed = WorkUnit(
        key="mixed-album", kind="album", album="Agaetis byrjun", album_artist="Sigur Ros"
    )
    db_session.add(group_mixed)
    db_session.flush()
    t_mixed_in.work_unit_id = group_mixed.id
    t_mixed_out.work_unit_id = group_mixed.id
    # Out-of-scope only group must be skipped too.
    t_out = _make_track(
        db_session,
        path=str(_write_fixture(sub_b / "out.mp3")),
        title="Intro",
        album="Far Album",
        album_artist="Far Artist",
        duration_ms=100_000,
    )
    group_out = WorkUnit(key="out-album", kind="album", album="Far Album")
    db_session.add(group_out)
    db_session.flush()
    t_out.work_unit_id = group_out.id
    from muzilla.db.models import ImportSession

    import_session = ImportSession(library_root=str(sub_a), stats={})
    db_session.add(import_session)
    db_session.flush()
    db_session.commit()
    job = enqueue(
        db_session,
        type="match",
        payload={"root": str(sub_a), "import_session_id": import_session.id},
    )
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)
    result = await handle_match(db_session, job, progress, _context(library_root=library))
    assert result["proposed"] == 1
    reviews = list(db_session.query(ReviewBundle).all())
    assert len(reviews) == 1
    assert reviews[0].scope_id == group_scoped.id
    # Mixed and out-of-scope groups keep unmatched state, no review created.
    db_session.expire_all()
    assert db_session.get(WorkUnit, group_mixed.id).match_state == "unmatched"  # type: ignore[union-attr]
    assert db_session.get(WorkUnit, group_out.id).match_state == "unmatched"  # type: ignore[union-attr]
    assert db_session.get(WorkUnit, group_scoped.id).match_state == "proposed"  # type: ignore[union-attr]
