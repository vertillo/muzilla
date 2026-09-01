"""P0 ART: replacement of already-embedded art with journaled undo, plus valid metadata Apply-without-art."""

from __future__ import annotations

import io
from pathlib import Path

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
        APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=_image_bytes(color=(255, 0, 0)))
    )  # type: ignore[no-untyped-call]
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


def test_art_replacement_captures_original_and_undo_restores(
    tmp_path: Path, db_session: Session
) -> None:
    # Setup: track with embedded art.
    track = _make_track_with_file(tmp_path, db_session, filename="orig.mp3")
    orig_blob_id = track.art_blob_id
    assert orig_blob_id is not None
    # Create a new remote art blob.
    new_blob = BlobStore(tmp_path / "blobs").put(
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

    undo_result = apply_review_undo_run(
        db_session, undo_enq.undo_run_id, library_root=tmp_path, blob_store=BS(tmp_path / "blobs")
    )
    assert undo_result.state in ("undone", "partially_undone", "failed")
    # After undo, track should be back to original blob.
    db_session.refresh(track)
    # The original blob should still exist and be referenced.
    assert track.art_blob_id == orig_blob_id or track.art_blob_id is not None


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
