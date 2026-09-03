from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.tags.reader import TagReadError
from muzilla.tags.support import (
    SUPPORTED_FORMATS,
    UNSUPPORTED_EXTENSIONS,
    UNSUPPORTED_FORMATS,
    classify_extension,
    classify_format,
)


@pytest.mark.parametrize("fmt", sorted(SUPPORTED_FORMATS))
def test_supported_format_classification(fmt: str) -> None:
    assert classify_format(fmt) == "supported"


@pytest.mark.parametrize("fmt", sorted(UNSUPPORTED_FORMATS))
def test_unsupported_format_classification(fmt: str) -> None:
    assert classify_format(fmt) == "unsupported"


@pytest.mark.parametrize("ext", sorted(UNSUPPORTED_EXTENSIONS))
def test_unsupported_extension_is_rejected(ext: str) -> None:
    assert classify_extension(ext) == "unsupported"


def test_unsupported_file_is_not_treated_as_audio(tmp_path: Path) -> None:
    from muzilla.pipeline.scan import AUDIO_EXTENSIONS

    for ext in UNSUPPORTED_EXTENSIONS:
        assert ext not in AUDIO_EXTENSIONS


def test_corrupt_wavpack_like_file_raises(tmp_path: Path) -> None:
    from muzilla.tags.reader import read_track

    # Create a dummy .wv file with garbage - must raise TagReadError, not be silently accepted
    p = tmp_path / "dummy.wv"
    p.write_bytes(b"not a wavpack" * 100)
    with pytest.raises(TagReadError):
        read_track(p)


def test_supported_fixtures_are_all_readable() -> None:
    from muzilla.tags.reader import read_track

    fixtures = Path(__file__).parent.parent / "fixtures" / "audio"
    for ext in [".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aiff"]:
        meta = read_track(fixtures / f"silence{ext}")
        assert meta.title is not None


@pytest.mark.parametrize("fmt", ["mp3", "flac", "ogg", "opus", "m4a", "wav", "aiff"])
def test_reviewed_apply_and_undo_round_trips_for_supported_formats(
    fmt: str, tmp_path: Path, db_session: Session
) -> None:
    """Per-supported-format reviewed Apply/Undo via actual ReviewBundle + journal."""
    import shutil

    from muzilla.changes.blobstore import BlobStore
    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.changes.bundle_undo import apply_review_undo_run
    from muzilla.config.schema import Config
    from muzilla.db.models import Track
    from muzilla.domain.reviews import BundleState
    from muzilla.pipeline.reviews import OperationDraft, put_revision, transition_bundle
    from muzilla.tags.reader import read_track

    fixtures = Path(__file__).parent.parent / "fixtures" / "audio"
    src = fixtures / f"silence.{fmt}"
    dst = tmp_path / f"test.{fmt}"
    shutil.copy(src, dst)
    # Create Track row via manual insert (scan would also work but this is focused)
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    track = Track(
        path=str(dst),
        filename=dst.name,
        ext=f".{fmt}",
        size_bytes=dst.stat().st_size,
        mtime_ns=dst.stat().st_mtime_ns,
        title="OriginalTitle",
        first_seen_at=now,
        last_scanned_at=now,
    )
    db_session.add(track)
    db_session.flush()
    # Config for applier (library_root, blob store)
    cfg = Config()
    cfg.storage.library_root = tmp_path
    cfg.storage.blob_dir = tmp_path / "blobs"
    cfg.storage.data_dir = tmp_path / "data"
    # Create ReviewBundle that changes title - compute real tag_hash
    from muzilla.domain.metadata import tag_hash as _tag_hash

    real_meta = read_track(dst)
    real_hash = _tag_hash(real_meta)
    # Update track's cached tag_hash to match real file
    track.tag_hash = real_hash
    track.size_bytes = dst.stat().st_size
    track.mtime_ns = dst.stat().st_mtime_ns
    db_session.flush()
    write = put_revision(
        db_session,
        logical_key=f"track:{track.id}",
        title=f"Review {track.filename}",
        scope_type="track",
        scope_id=track.id,
        source_snapshot={
            "items": [
                {
                    "source_type": "track",
                    "source_id": track.id,
                    "path": track.path,
                    "filename": track.filename,
                    "size_bytes": track.size_bytes,
                    "mtime_ns": track.mtime_ns,
                    "tag_hash": real_hash,
                }
            ]
        },
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value="OriginalTitle",
                proposed_value="FormatApplyTest",
            ),
        ),
    )
    from muzilla.db.models import Operation

    op = db_session.scalar(select(Operation).where(Operation.proposal_revision_id == write.revision_id))
    assert op is not None
    op.decision = "accepted"
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    db_session.commit()
    # Apply via bundle applier (journaled) - enqueue first to get ApplyRun
    from muzilla.services.review_apply import enqueue_review_apply

    enq = enqueue_review_apply(db_session, write.bundle_id, idempotency_key=f"apply-{fmt}")
    result = apply_review_run(
        db_session,
        enq.apply_run_id,
        library_root=tmp_path,
        create_directories=False,
        blob_store=BlobStore(tmp_path / "blobs"),
        backup_store=None,
        should_cancel=lambda: False,
    )
    assert result.state in ("applied", "succeeded")
    after_apply = read_track(dst)
    assert after_apply.title == "FormatApplyTest"
    # Undo via persistent undo (journal)
    from muzilla.db.models import ApplyRun
    from muzilla.services.review_undo import enqueue_review_undo

    apply_run = db_session.query(ApplyRun).filter_by(review_bundle_id=write.bundle_id).first()  # nosec
    assert apply_run is not None
    undo_enq = enqueue_review_undo(
        db_session, write.bundle_id, apply_run_id=apply_run.id, idempotency_key=f"undo-{fmt}"
    )
    undo_result = apply_review_undo_run(
        db_session,
        undo_enq.undo_run_id,
        library_root=tmp_path,
        create_directories=False,
        blob_store=BlobStore(tmp_path / "blobs"),
        backup_store=None,
        should_cancel=lambda: False,
    )
    assert undo_result.state in ("undone", "succeeded", "applied")
    after_undo = read_track(dst)
    assert after_undo.title == "OriginalTitle"
