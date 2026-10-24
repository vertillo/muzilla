# ponytail: deterministic REVIEW-CONFLICTS-001 coverage - drift and concurrent
import os
import shutil
from pathlib import Path

from sqlalchemy import func, select  # pyright: ignore
from sqlalchemy.orm import Session  # pyright: ignore

from muzilla.changes.blobstore import BlobStore
from muzilla.db.models import ReviewBundle
from muzilla.domain.reviews import BundleState
from muzilla.pipeline.reviews import put_revision, transition_bundle
from muzilla.services.reviews import OperationDraft

FIXTURE_MP3 = Path(__file__).parent.parent / "fixtures/audio/silence.mp3"


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _track(session: Session, path: Path) -> int:
    from datetime import UTC, datetime

    from muzilla.db.models import Track
    from muzilla.domain.metadata import tag_hash as th
    from muzilla.tags.reader import read_track

    stat = path.stat()
    meta = read_track(path)
    h = th(meta)
    now = datetime.now(UTC)
    t = Track(
        path=str(path),
        filename=path.name,
        ext=path.suffix,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        title=meta.title or "t",
        artist=meta.artist or "a",
        tag_hash=h,
        first_seen_at=now,
        last_scanned_at=now,
    )
    session.add(t)
    session.flush()
    return t.id


def _bundle(
    session: Session,
    tid: int,
    suffix: str = "conflict",
    *,
    library_root: Path | None = None,
    source_items: list[dict[str, object]] | None = None,
) -> int:
    from muzilla.changes.writer import capture_file_guard
    from muzilla.db.models import Track

    tr = session.get(Track, tid)
    assert tr is not None
    root = library_root or Path(tr.path).parent
    items = source_items
    if items is None:
        items = [
            {
                "source_type": "track",
                "source_id": tid,
                "path": tr.path,
                "size_bytes": tr.size_bytes,
                "mtime_ns": tr.mtime_ns,
                "tag_hash": tr.tag_hash,
                "filename": tr.filename,
                "physical_guard": capture_file_guard(Path(tr.path), root),
            }
        ]
    ops = [
        OperationDraft(
            kind="set_tag",
            field="title",
            target_type="track",
            target_id=tid,
            current_value=tr.title,
            proposed_value="New",
        )
    ]
    w = put_revision(
        session,
        logical_key=f"track:{tid}:{suffix}",
        title="conflict",
        scope_type="track",
        scope_id=tid,
        source_snapshot={"items": items},
        operations=tuple(ops),
    )
    for op in session.scalars(
        select(__import__("muzilla.db.models", fromlist=["Operation"]).Operation).where(
            __import__("muzilla.db.models", fromlist=["Operation"]).Operation.proposal_revision_id
            == w.revision_id
        )
    ):
        op.decision = "accepted"
    transition_bundle(session, w.bundle_id, BundleState.READY)
    session.commit()
    return w.bundle_id


def test_drift_mtime_preserving_tag_blocks_whole_bundle(
    tmp_path: Path, db_session: Session
) -> None:
    lib = tmp_path / "lib"
    lib.mkdir()
    f = lib / "a.mp3"
    _copy(FIXTURE_MP3, f)
    tid = _track(db_session, f)
    bid = _bundle(db_session, tid, library_root=lib)
    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.pipeline.reviews import start_apply_run

    run = start_apply_run(db_session, bid, idempotency_key="drift-1")
    db_session.commit()
    # Change only mtime deterministically; tag_hash remains unchanged.
    stat_before = f.stat()
    os.utime(f, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns + 1_000_000_000))
    # ensure size same, mtime different, tag_hash same
    from muzilla.domain.metadata import tag_hash as th
    from muzilla.tags.reader import read_track

    # re-read to confirm tag_hash unchanged
    _track_obj = db_session.get(__import__("muzilla.db.models", fromlist=["Track"]).Track, tid)
    assert _track_obj is not None
    assert th(read_track(f)) == _track_obj.tag_hash
    result = apply_review_run(
        db_session, run.id, library_root=lib, blob_store=BlobStore(tmp_path / "b")
    )
    assert result.state == "failed"
    assert any(
        "stale" in (e or "") or "stat changed" in (e or "") for e in result.errors.values()
    ) or "stale" in (db_session.get(ReviewBundle, bid).error or "")  # type: ignore[union-attr]
    # whole bundle blocked - no journal
    from muzilla.db.models import ReviewFileJournal

    j = list(
        db_session.scalars(
            select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == run.id)
        )
    )
    assert len(j) == 0


def test_worker_rejects_same_stat_non_tag_byte_drift_before_journaling(
    tmp_path: Path, db_session: Session
) -> None:
    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.db.models import ReviewFileJournal, Track
    from muzilla.domain.metadata import tag_hash
    from muzilla.pipeline.reviews import start_apply_run
    from muzilla.tags.reader import read_track

    library = tmp_path / "physical-drift"
    library.mkdir()
    source = library / "same-stat.mp3"
    _copy(FIXTURE_MP3, source)
    track_id = _track(db_session, source)
    track = db_session.get(Track, track_id)
    assert track is not None
    original_tag_hash = track.tag_hash
    bundle_id = _bundle(db_session, track_id, library_root=library)
    run = start_apply_run(db_session, bundle_id, idempotency_key="physical-drift")
    db_session.commit()

    original_stat = source.stat()
    changed = bytearray(source.read_bytes())
    changed[-1] ^= 1
    source.write_bytes(changed)
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert source.stat().st_size == original_stat.st_size
    assert source.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert tag_hash(read_track(source)) == original_tag_hash
    changed_content = source.read_bytes()

    result = apply_review_run(
        db_session, run.id, library_root=library, blob_store=BlobStore(tmp_path / "blobs")
    )

    assert result.state == "failed"
    assert any("physical content changed" in (error or "") for error in result.errors.values())
    assert source.read_bytes() == changed_content
    assert (
        list(
            db_session.scalars(
                select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == run.id)
            )
        )
        == []
    )


def test_worker_rejects_manifest_omitting_accepted_target_before_journaling(
    tmp_path: Path, db_session: Session
) -> None:
    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.db.models import ApplyRun, ReviewFileJournal
    from muzilla.pipeline.reviews import start_apply_run

    library = tmp_path / "missing-target"
    library.mkdir()
    source = library / "target.mp3"
    _copy(FIXTURE_MP3, source)
    track_id = _track(db_session, source)
    bundle_id = _bundle(db_session, track_id, library_root=library)
    run = start_apply_run(db_session, bundle_id, idempotency_key="manifest-missing-target")
    run.manifest = {**run.manifest, "files": []}
    db_session.commit()
    original_content = source.read_bytes()

    result = apply_review_run(
        db_session, run.id, library_root=library, blob_store=BlobStore(tmp_path / "blobs")
    )

    assert result.state == "failed"
    assert run.error is not None and "no per-file entries" in run.error
    assert result.files == ()
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(ReviewFileJournal)) == 0
    assert source.read_bytes() == original_content


def test_worker_rejects_partial_manifest_target_omission_before_journaling(
    tmp_path: Path, db_session: Session
) -> None:
    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.changes.writer import capture_file_guard
    from muzilla.db.models import ApplyRun, Operation, ReviewFileJournal, Track
    from muzilla.pipeline.reviews import start_apply_run

    library = tmp_path / "partial-manifest"
    library.mkdir()
    paths = [library / "first.mp3", library / "second.mp3"]
    for path in paths:
        _copy(FIXTURE_MP3, path)
    track_ids = [_track(db_session, path) for path in paths]
    tracks = [db_session.get(Track, track_id) for track_id in track_ids]
    assert all(track is not None for track in tracks)
    source_items: list[dict[str, object]] = []
    operations: list[OperationDraft] = []
    for track in tracks:
        assert track is not None
        source_items.append(
            {
                "source_type": "track",
                "source_id": track.id,
                "path": track.path,
                "size_bytes": track.size_bytes,
                "mtime_ns": track.mtime_ns,
                "tag_hash": track.tag_hash,
                "physical_guard": capture_file_guard(Path(track.path), library),
            }
        )
        operations.append(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value=track.title,
                proposed_value=f"Changed {track.id}",
            )
        )
    write = put_revision(
        db_session,
        logical_key="group:partial-manifest",
        title="Partial apply manifest",
        scope_type="group",
        scope_id=1,
        source_snapshot={"items": source_items},
        operations=tuple(operations),
    )
    for operation in db_session.scalars(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    ):
        operation.decision = "accepted"
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    run = start_apply_run(db_session, write.bundle_id, idempotency_key="partial-manifest-target")
    files = run.manifest.get("files")
    assert isinstance(files, list) and len(files) == 2
    run.manifest = {**run.manifest, "files": files[:1]}
    db_session.commit()
    original_bytes = [path.read_bytes() for path in paths]

    result = apply_review_run(
        db_session,
        run.id,
        library_root=library,
        blob_store=BlobStore(tmp_path / "blobs"),
    )

    assert result.state == "failed"
    assert run.error is not None and "does not match accepted operations" in run.error
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(ReviewFileJournal)) == 0
    assert [path.read_bytes() for path in paths] == original_bytes


def test_concurrent_apply_same_track_second_gets_409(tmp_path: Path, db_session: Session) -> None:
    lib = tmp_path / "lib2"
    lib.mkdir()
    f = lib / "b.mp3"
    _copy(FIXTURE_MP3, f)
    tid = _track(db_session, f)
    # two bundles sharing same track (different logical_key but same source_id)
    b1 = _bundle(db_session, tid, library_root=lib)
    # second bundle with different logical_key but same track
    # manually create second bundle via put_revision with different logical_key
    from muzilla.changes.writer import capture_file_guard
    from muzilla.db.models import Track

    tr = db_session.get(Track, tid)
    assert tr is not None
    items = [
        {
            "source_type": "track",
            "source_id": tid,
            "path": tr.path,
            "size_bytes": tr.size_bytes,
            "mtime_ns": tr.mtime_ns,
            "tag_hash": tr.tag_hash,
            "filename": tr.filename,
            "physical_guard": capture_file_guard(Path(tr.path), lib),
        }
    ]
    ops = [
        OperationDraft(
            kind="set_tag",
            field="title",
            target_type="track",
            target_id=tid,
            current_value=tr.title,
            proposed_value="New2",
        )
    ]
    w2 = put_revision(
        db_session,
        logical_key=f"track:{tid}:conflict2",
        title="conflict2",
        scope_type="track",
        scope_id=tid,
        source_snapshot={"items": items},
        operations=tuple(ops),
    )
    for op in db_session.scalars(
        select(__import__("muzilla.db.models", fromlist=["Operation"]).Operation).where(
            __import__("muzilla.db.models", fromlist=["Operation"]).Operation.proposal_revision_id
            == w2.revision_id
        )
    ):
        op.decision = "accepted"
    transition_bundle(db_session, w2.bundle_id, BundleState.READY)
    db_session.commit()
    b2 = w2.bundle_id
    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.pipeline.reviews import start_apply_run

    r1 = start_apply_run(db_session, b1, idempotency_key="conc-1")
    r2 = start_apply_run(db_session, b2, idempotency_key="conc-2")
    db_session.commit()
    # keep r1 pending to simulate concurrent
    assert r1.id != r2.id
    # first apply will be pending, second preflight should detect concurrent
    # simulate concurrent by keeping r1 pending and trying r2 preflight
    # r1 is pending, r2 apply should block
    result2 = apply_review_run(
        db_session, r2.id, library_root=lib, blob_store=BlobStore(tmp_path / "b2")
    )
    assert result2.state == "failed"
    _b2 = db_session.get(ReviewBundle, b2)
    assert _b2 is not None
    assert any("concurrent" in (e or "") for e in result2.errors.values()) or "concurrent" in (
        _b2.error or ""
    )
    # also test service-level 409
    from muzilla.services.review_apply import enqueue_review_apply

    # r1 still pending, enqueue for b2 should raise concurrent
    try:
        enqueue_review_apply(db_session, b2, idempotency_key="conc-3", library_root=lib)
        raise AssertionError("expected concurrent 409")
    except Exception as exc:
        assert "concurrent" in str(exc).lower()


def test_worker_rejects_incomplete_competing_review_before_journaling(
    tmp_path: Path, db_session: Session
) -> None:
    library = tmp_path / "incomplete-active-review"
    library.mkdir()
    source = library / "shared.mp3"
    _copy(FIXTURE_MP3, source)
    track_id = _track(db_session, source)
    _bundle(
        db_session,
        track_id,
        "incomplete",
        library_root=library,
        source_items=[],
    )
    applying_bundle_id = _bundle(db_session, track_id, "applying-incomplete", library_root=library)

    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.db.models import ApplyRun, ReviewFileJournal
    from muzilla.pipeline.reviews import start_apply_run

    run = start_apply_run(
        db_session, applying_bundle_id, idempotency_key="incomplete-active-review"
    )
    db_session.commit()
    original_content = source.read_bytes()

    result = apply_review_run(
        db_session, run.id, library_root=library, blob_store=BlobStore(tmp_path / "blobs")
    )

    assert result.state == "failed"
    assert any("concurrent" in error for error in result.errors.values())
    assert source.read_bytes() == original_content
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(ReviewFileJournal)) == 0


def test_worker_rejects_active_review_without_current_revision_before_journaling(
    tmp_path: Path, db_session: Session
) -> None:
    library = tmp_path / "missing-current-revision"
    library.mkdir()
    source = library / "shared.mp3"
    _copy(FIXTURE_MP3, source)
    track_id = _track(db_session, source)
    applying_bundle_id = _bundle(
        db_session, track_id, "applying-missing-revision", library_root=library
    )

    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.db.models import ApplyRun, ReviewBundle, ReviewFileJournal
    from muzilla.pipeline.reviews import start_apply_run

    db_session.add(
        ReviewBundle(
            logical_key=f"track:{track_id}:missing-current-revision",
            title="Incomplete active review",
            scope_type="track",
            scope_id=track_id,
            state=BundleState.READY.value,
        )
    )
    run = start_apply_run(
        db_session, applying_bundle_id, idempotency_key="missing-current-revision"
    )
    db_session.commit()
    original_content = source.read_bytes()

    result = apply_review_run(
        db_session, run.id, library_root=library, blob_store=BlobStore(tmp_path / "blobs")
    )

    assert result.state == "failed"
    assert any("concurrent" in error for error in result.errors.values())
    assert source.read_bytes() == original_content
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(ReviewFileJournal)) == 0


def test_apply_blocks_an_active_review_bundle_for_same_source(
    tmp_path: Path, db_session: Session
) -> None:
    library = tmp_path / "active-review"
    library.mkdir()
    source = library / "shared.mp3"
    _copy(FIXTURE_MP3, source)
    track_id = _track(db_session, source)
    _bundle(db_session, track_id, "active", library_root=library)
    applying_bundle = _bundle(db_session, track_id, "applying", library_root=library)

    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.db.models import ReviewFileJournal
    from muzilla.pipeline.reviews import start_apply_run

    run = start_apply_run(db_session, applying_bundle, idempotency_key="active-review")
    db_session.commit()
    original_content = source.read_bytes()

    result = apply_review_run(
        db_session, run.id, library_root=library, blob_store=BlobStore(tmp_path / "blobs")
    )

    assert result.state == "failed"
    assert any("concurrent" in error for error in result.errors.values())
    assert source.read_bytes() == original_content
    assert (
        list(
            db_session.scalars(
                select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == run.id)
            )
        )
        == []
    )
