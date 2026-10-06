"""P0 ART: replacement of already-embedded art with journaled undo, plus valid metadata Apply-without-art."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.blobstore import BlobStore
from muzilla.changes.bundle_applier import apply_review_run
from muzilla.db.models import ReviewFileJournal, Track
from muzilla.domain.reviews import BundleState
from muzilla.pipeline.reviews import OperationDraft, put_revision, transition_bundle
from muzilla.services.reviews import get_review_bundle


def _image_bytes(
    color: tuple[int, int, int] = (10, 20, 30),
    format: str = "JPEG",
    size: tuple[int, int] = (100, 100),
) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format=format)
    return buf.getvalue()


def _make_track_with_file(tmp_path: Path, db_session: Session, *, filename: str) -> Track:
    import shutil

    from mutagen.id3 import APIC, ID3

    file_path = tmp_path / filename
    # Use a real valid MP3 fixture so mutagen can read/write tags correctly.
    shutil.copy(Path("tests/fixtures/audio/silence.mp3"), file_path)
    try:
        id3 = ID3(str(file_path))  # type: ignore[no-untyped-call]
    except Exception:
        id3 = ID3()  # type: ignore[no-untyped-call]
    id3.add(
        APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=_image_bytes(color=(255, 0, 0)))  # type: ignore[no-untyped-call]
    )
    id3.save(str(file_path))
    # Ensure the file exists and is readable.
    if not file_path.exists() or file_path.stat().st_size == 0:
        shutil.copy(Path("tests/fixtures/audio/silence.mp3"), file_path)
    # Create a Track row for it.
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    track = Track(
        path=str(file_path),
        filename=file_path.name,
        ext=".mp3",
        size_bytes=file_path.stat().st_size,
        mtime_ns=file_path.stat().st_mtime_ns,
        title="Old",
        artist="Artist",
        first_seen_at=now,
        last_scanned_at=now,
        has_embedded_art=True,
    )
    db_session.add(track)
    db_session.flush()
    # Also create a blob for the original art so the DB's art_blob_id is set.
    blob = BlobStore(tmp_path / "blobs").put(
        db_session, _image_bytes(color=(255, 0, 0)), mime="image/jpeg", width=100, height=100
    )
    blob.width, blob.height = 100, 100
    db_session.flush()
    track.art_blob_id = blob.id
    db_session.commit()
    return track


@pytest.mark.parametrize(
    "inverse_blob_fault",
    [None, "catalog_missing", "catalog_corrupt", "embedded_missing", "embedded_corrupt"],
)
def test_art_replacement_captures_original_and_undo_restores(
    tmp_path: Path, db_session: Session, inverse_blob_fault: str | None
) -> None:
    # Setup: track with embedded art.
    track = _make_track_with_file(tmp_path, db_session, filename="orig.mp3")
    track_id = track.id
    store = BlobStore(tmp_path / "blobs")
    catalog_blob = store.put(
        db_session,
        _image_bytes(color=(0, 0, 255)),
        mime="image/jpeg",
        width=100,
        height=100,
    )
    track.art_blob_id = catalog_blob.id
    db_session.commit()
    orig_blob_id = track.art_blob_id
    assert orig_blob_id is not None
    # Create a new remote art blob.
    new_blob = store.put(
        db_session, _image_bytes(color=(0, 255, 0)), mime="image/jpeg", width=100, height=100
    )
    new_blob.width, new_blob.height = 100, 100
    db_session.flush()
    # Create a ReviewBundle that replaces art.

    # Create a candidate that will generate an embed_art operation.
    from pathlib import Path as _P

    from muzilla.domain.metadata import tag_hash as _tag_hash
    from muzilla.domain.reviews import OperationKind
    from muzilla.tags.reader import read_track as _read_track

    _meta2 = _read_track(_P(track.path))
    _th2 = _tag_hash(_meta2)
    # Directly create a revision with embed_art for simplicity.
    write = put_revision(
        db_session,
        logical_key=f"track:{track.id}",
        title="Review art replace",
        scope_type="track",
        scope_id=track.id,
        source_snapshot={
            "items": [
                {
                    "source_type": "track",
                    "source_id": track.id,
                    "path": track.path,
                    "size_bytes": track.size_bytes,
                    "mtime_ns": track.mtime_ns,
                    "tag_hash": _th2,
                    "content_hash": track.content_hash,
                }
            ]
        },
        operations=(
            OperationDraft(
                kind=OperationKind.EMBED_ART,
                field="art",
                target_type="track",
                target_id=track.id,
                current_value={"blob_id": orig_blob_id} if orig_blob_id else None,
                proposed_value={"blob_id": new_blob.id},
                provenance={"section": "cover", "provider": "coverartarchive"},
            ),
        ),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    from muzilla.services.reviews import apply_operation_decisions

    detail = get_review_bundle(db_session, write.bundle_id)
    assert detail is not None
    op_id = detail.current_revision.operations[0].id
    apply_operation_decisions(
        db_session, write.bundle_id, revision_id=write.revision_id, decisions=((op_id, "accepted"),)
    )
    db_session.commit()
    # Enqueue and apply.
    from muzilla.changes.blobstore import BlobStore as BS
    from muzilla.services.review_apply import enqueue_review_apply

    enqueued = enqueue_review_apply(
        db_session, write.bundle_id, idempotency_key="test-art-replace", backup=False
    )
    db_session.commit()
    # Apply.
    result = apply_review_run(
        db_session,
        enqueued.apply_run_id,
        library_root=tmp_path,
        blob_store=BS(tmp_path / "blobs"),
    )
    assert result.state == "applied"
    # Verify file now has new art and DB points to new blob.
    db_session.refresh(track)
    assert track.art_blob_id == new_blob.id
    # Verify journal captured original.
    journals = list(
        db_session.scalars(
            select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == enqueued.apply_run_id)
        )
    )
    # Find the tags journal.
    tag_journal = next((j for j in journals if j.phase == "tags"), None)
    assert tag_journal is not None
    assert tag_journal.before_blob.get("__muzilla_art_blob_id") in (
        orig_blob_id,
        tag_journal.before_blob.get("__muzilla_before_art_blob_id"),
    )
    # Now undo.
    from muzilla.db.models import Job, ReviewUndoRun
    from muzilla.services.review_undo import enqueue_review_undo

    undo_enq = enqueue_review_undo(
        db_session,
        write.bundle_id,
        apply_run_id=enqueued.apply_run_id,
        idempotency_key="undo-art",
        backup=False,
    )
    db_session.commit()
    from muzilla.changes.bundle_undo import apply_review_undo_run

    store = BS(tmp_path / "blobs")
    undo_run = db_session.get(ReviewUndoRun, undo_enq.undo_run_id)
    assert undo_run is not None and isinstance(undo_run.manifest, dict)
    manifest_files = undo_run.manifest.get("files")
    assert isinstance(manifest_files, list) and len(manifest_files) == 1
    file_entry = manifest_files[0]
    assert isinstance(file_entry, dict) and isinstance(file_entry.get("steps"), list)
    tag_step = next(step for step in file_entry["steps"] if step.get("phase") == "tags")
    before_blob = tag_step.get("before_blob")
    assert isinstance(before_blob, dict)
    embedded_art = before_blob.get("__muzilla_embedded_art")
    assert isinstance(embedded_art, dict) and isinstance(embedded_art.get("entries"), list)
    embedded_entries = embedded_art["entries"]
    assert embedded_entries and isinstance(embedded_entries[0], dict)
    embedded_blob_id = embedded_entries[0].get("blob_id")
    assert isinstance(embedded_blob_id, int) and embedded_blob_id != orig_blob_id

    catalog_inverse = store.get_by_id(db_session, orig_blob_id)
    embedded_inverse = store.get_by_id(db_session, embedded_blob_id)
    assert catalog_inverse is not None and embedded_inverse is not None
    catalog_blob_bytes = store.get_durable_bytes(catalog_inverse)
    embedded_blob_bytes = store.get_durable_bytes(embedded_inverse)
    catalog_blob_path = store.root / catalog_inverse.storage_path
    embedded_blob_path = store.root / embedded_inverse.storage_path
    applied_path = Path(track.path)
    applied_bytes = applied_path.read_bytes()
    applied_inode = applied_path.stat().st_ino
    fault_blob_path: Path | None = None
    fault_blob_bytes: bytes | None = None
    if inverse_blob_fault is not None:
        fault_area, fault_kind = inverse_blob_fault.split("_", 1)
        fault_blob_path, fault_blob_bytes = (
            (catalog_blob_path, catalog_blob_bytes)
            if fault_area == "catalog"
            else (embedded_blob_path, embedded_blob_bytes)
        )
        if fault_kind == "missing":
            fault_blob_path.unlink()
        else:
            corrupted = bytearray(fault_blob_bytes)
            corrupted[len(corrupted) // 2] ^= 1
            fault_blob_path.write_bytes(corrupted)

    undo_result = apply_review_undo_run(
        db_session, undo_enq.undo_run_id, library_root=tmp_path, blob_store=store
    )
    if inverse_blob_fault is None:
        assert undo_result.state == "undone", undo_result
        db_session.refresh(track)
        assert track.art_blob_id == orig_blob_id
        from muzilla.tags.writer import capture_embedded_art

        assert capture_embedded_art(applied_path)["entries"][0]["data"] == _image_bytes(
            color=(255, 0, 0)
        )
        return

    assert undo_result.state == "failed"
    assert undo_result.recovery_required
    assert applied_path.read_bytes() == applied_bytes
    assert applied_path.stat().st_ino == applied_inode
    failed_run = db_session.get(ReviewUndoRun, undo_enq.undo_run_id)
    assert failed_run is not None and failed_run.result is not None
    assert failed_run.result["retryable"] is True
    initial_job = db_session.get(Job, undo_enq.job_id)
    assert initial_job is not None
    initial_job.state = "failed"
    db_session.commit()

    engine = db_session.get_bind()
    from sqlalchemy.engine import Engine

    assert isinstance(engine, Engine)
    database = engine.url.database
    assert isinstance(database, str)
    db_path = Path(database)
    db_session.close()
    engine.dispose()
    assert fault_blob_path is not None and fault_blob_bytes is not None
    fault_blob_path.write_bytes(fault_blob_bytes)

    from muzilla.db.engine import create_db_engine, create_session_factory
    from muzilla.pipeline.retention import sweep_apply_journals
    from muzilla.tags.writer import capture_embedded_art

    reopened_engine = create_db_engine(db_path)
    reopened_factory = create_session_factory(reopened_engine)
    try:
        with reopened_factory() as reopened_session:
            assert sweep_apply_journals(
                reopened_session, journal_days=0, journal_changesets=500
            ) == (0, 0)
            reopened_session.commit()
            assert sweep_apply_journals(
                reopened_session, journal_days=3650, journal_changesets=0
            ) == (0, 0)
            reopened_session.commit()
            retained_journals = list(
                reopened_session.scalars(
                    select(ReviewFileJournal).where(
                        ReviewFileJournal.apply_run_id == enqueued.apply_run_id
                    )
                )
            )
            assert len(retained_journals) == len(journals)
            retained_catalog = store.get_by_id(reopened_session, orig_blob_id)
            retained_embedded = store.get_by_id(reopened_session, embedded_blob_id)
            assert retained_catalog is not None and retained_embedded is not None
            assert store.get_durable_bytes(retained_catalog) == catalog_blob_bytes
            assert store.get_durable_bytes(retained_embedded) == embedded_blob_bytes

            retry_enq = enqueue_review_undo(
                reopened_session,
                write.bundle_id,
                apply_run_id=enqueued.apply_run_id,
                idempotency_key=f"undo-art-retry-{inverse_blob_fault}",
                backup=False,
            )
            reopened_session.commit()
            retried = apply_review_undo_run(
                reopened_session,
                retry_enq.undo_run_id,
                library_root=tmp_path,
                blob_store=store,
            )
            assert retried.state == "undone", retried
            restored_track = reopened_session.get(Track, track_id)
            assert restored_track is not None and restored_track.art_blob_id == orig_blob_id
            assert capture_embedded_art(applied_path)["entries"][0]["data"] == _image_bytes(
                color=(255, 0, 0)
            )
    finally:
        reopened_engine.dispose()


def test_metadata_apply_without_art_is_valid(tmp_path: Path, db_session: Session) -> None:
    # Create a track without art, and a review that only changes metadata (no embed_art).
    import shutil
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    file_path = tmp_path / "noart.mp3"
    # Use a real valid MP3 fixture so read_track succeeds.
    shutil.copy(Path("tests/fixtures/audio/silence.mp3"), file_path)
    track = Track(
        path=str(file_path),
        filename=file_path.name,
        ext=".mp3",
        size_bytes=file_path.stat().st_size,
        mtime_ns=file_path.stat().st_mtime_ns,
        title="Old",
        artist="Artist",
        first_seen_at=now,
        last_scanned_at=now,
        has_embedded_art=False,
    )
    db_session.add(track)
    db_session.flush()
    # Build a proper source snapshot with the required precondition fields.
    from muzilla.domain.metadata import tag_hash as _tag_hash
    from muzilla.domain.reviews import OperationKind
    from muzilla.pipeline.reviews import OperationDraft
    from muzilla.tags.reader import read_track as _read_track

    _meta = _read_track(file_path)
    _th = _tag_hash(_meta)
    write = put_revision(
        db_session,
        logical_key=f"track:{track.id}",
        title="Review metadata only",
        scope_type="track",
        scope_id=track.id,
        source_snapshot={
            "items": [
                {
                    "source_type": "track",
                    "source_id": track.id,
                    "path": track.path,
                    "size_bytes": track.size_bytes,
                    "mtime_ns": track.mtime_ns,
                    "tag_hash": _th,
                    "content_hash": track.content_hash,
                }
            ]
        },
        operations=(
            OperationDraft(
                kind=OperationKind.SET_TAG,
                field="title",
                target_type="track",
                target_id=track.id,
                current_value="Old",
                proposed_value="New",
            ),
        ),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    detail = get_review_bundle(db_session, write.bundle_id)
    assert detail is not None
    op_id = detail.current_revision.operations[0].id
    from muzilla.services.reviews import apply_operation_decisions

    apply_operation_decisions(
        db_session, write.bundle_id, revision_id=write.revision_id, decisions=((op_id, "accepted"),)
    )
    db_session.commit()
    from muzilla.changes.blobstore import BlobStore as BS
    from muzilla.services.review_apply import enqueue_review_apply

    enqueued = enqueue_review_apply(
        db_session, write.bundle_id, idempotency_key="test-no-art", backup=False
    )
    db_session.commit()
    result = apply_review_run(
        db_session, enqueued.apply_run_id, library_root=tmp_path, blob_store=BS(tmp_path / "blobs")
    )
    print(result)
    assert result.state == "applied", f"result={result} files={result.files} errors={result.errors}"
    db_session.refresh(track)
    assert track.title == "New"
    assert track.has_embedded_art is False
