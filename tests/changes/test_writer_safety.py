from __future__ import annotations

import errno
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux-only tests skip without fcntl
    fcntl = None  # type: ignore[assignment]

import pytest
from sqlalchemy.orm import Session

from muzilla.changes.blobstore import BlobStore
from muzilla.changes.bundle_applier import apply_review_run
from muzilla.changes.writer import _StagedReplacement, restore_from_before_blob
from muzilla.db.models import Track
from muzilla.domain.metadata import tag_hash
from muzilla.domain.reviews import BundleState, OperationKind
from muzilla.pipeline.reviews import put_revision, transition_bundle
from muzilla.services.review_apply import enqueue_review_apply
from muzilla.services.reviews import OperationDraft, apply_operation_decisions, get_review_bundle
from muzilla.tags.hashing import partial_content_hash
from muzilla.tags.reader import read_track


def _enqueue_title_change(
    *,
    tmp_path: Path,
    db_session: Session,
    path: Path,
    new_title: str,
    remove_art: bool = False,
    catalog_art_blob_id: int | None = None,
    move_after_tags: bool = False,
    move_destination: str | None = None,
) -> tuple[Track, int]:
    stat = path.stat()
    meta = read_track(path)
    current_hash = tag_hash(meta)
    now = datetime.now(UTC)
    track = Track(
        path=str(path),
        filename=path.name,
        ext=path.suffix,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        content_hash=partial_content_hash(path, stat.st_size),
        tag_hash=current_hash,
        title=meta.title or "Old",
        artist=meta.artist or "Artist",
        art_blob_id=catalog_art_blob_id,
        has_embedded_art=remove_art,
        first_seen_at=now,
        last_scanned_at=now,
    )
    db_session.add(track)
    db_session.flush()
    operations = [
        OperationDraft(
            kind=OperationKind.SET_TAG,
            field="title",
            target_type="track",
            target_id=track.id,
            current_value=track.title,
            proposed_value=new_title,
        )
    ]
    if remove_art:
        operations.append(
            OperationDraft(
                kind=OperationKind.REMOVE_ART,
                field="art",
                target_type="track",
                target_id=track.id,
                current_value={"blob_id": catalog_art_blob_id or 1},
                proposed_value=None,
            )
        )
    if move_after_tags:
        operations.append(
            OperationDraft(
                kind=OperationKind.MOVE_FILE,
                field="path",
                target_type="track",
                target_id=track.id,
                current_value=str(path),
                proposed_value=move_destination or f"{path.name}.renamed",
                provenance={"section": "path"},
                validation={"errors": [], "collision": False},
            )
        )
    write = put_revision(
        db_session,
        logical_key=f"track:{track.id}:safe-writer:{new_title}",
        title="Safe writer regression",
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
                    "tag_hash": current_hash,
                    "content_hash": track.content_hash,
                }
            ]
        },
        operations=tuple(operations),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    detail = get_review_bundle(db_session, write.bundle_id)
    assert detail is not None
    operation_ids = tuple(operation.id for operation in detail.current_revision.operations)
    apply_operation_decisions(
        db_session,
        write.bundle_id,
        revision_id=write.revision_id,
        decisions=tuple((operation_id, "accepted") for operation_id in operation_ids),
    )
    db_session.commit()
    enqueued = enqueue_review_apply(
        db_session,
        write.bundle_id,
        idempotency_key=f"safe-writer-{track.id}",
        backup=False,
    )
    db_session.commit()
    return track, enqueued.apply_run_id


def _library_entry_names(library: Path) -> list[str]:
    return sorted(entry.name for entry in library.iterdir() if entry.name != ".muzilla-private")


def test_apply_rejects_planted_temp_symlink_without_touching_sentinel_or_catalog(
    tmp_path: Path, db_session: Session
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    original_source = source.read_bytes()
    sentinel = tmp_path / "outside-sentinel.bin"
    sentinel.write_bytes(b"must remain unchanged")
    unrelated = library / "unrelated.bin"
    unrelated.write_bytes(b"also unchanged")
    temporary = source.with_name(source.name + ".muzilla.tmp")
    temporary.symlink_to(sentinel)
    original_sentinel = sentinel.read_bytes()
    original_unrelated = unrelated.read_bytes()

    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path, db_session=db_session, path=source, new_title="Changed"
    )
    result = apply_review_run(
        db_session,
        apply_run_id,
        library_root=library,
        blob_store=BlobStore(tmp_path / "blobs"),
    )

    assert result.state == "failed"
    assert not result.recovery_required
    assert source.read_bytes() == original_source
    assert read_track(source).title != "Changed"
    assert sentinel.read_bytes() == original_sentinel
    assert unrelated.read_bytes() == original_unrelated
    assert temporary.is_symlink()
    db_session.refresh(track)
    assert track.title != "Changed"


def test_apply_does_not_publish_over_source_replaced_during_tag_write(
    tmp_path: Path, db_session: Session
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    original_source = source.read_bytes()
    displaced = library / "concurrent-original.mp3"
    concurrent_bytes = b"concurrent replacement must survive"
    unrelated = library / "unrelated.txt"
    unrelated.write_bytes(b"unrelated")
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path, db_session=db_session, path=source, new_title="Changed"
    )

    from muzilla.tags.writer import write_fields as real_write_fields

    def replace_source(filething: Any, field_values: dict[str, Any]) -> None:
        real_write_fields(filething, field_values)
        os.replace(source, displaced)
        source.write_bytes(concurrent_bytes)

    with patch("muzilla.changes.writer.write_fields", side_effect=replace_source):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=BlobStore(tmp_path / "blobs"),
        )

    assert result.state == "failed"
    assert not result.recovery_required
    assert source.read_bytes() == concurrent_bytes
    assert displaced.read_bytes() == original_source
    assert unrelated.read_bytes() == b"unrelated"
    assert not list(library.glob(".song.mp3.muzilla-*.tmp"))
    db_session.refresh(track)
    assert track.title != "Changed"


@pytest.mark.parametrize(
    ("replacement_kind", "block_compensation"),
    [("regular", False), ("symlink", False), ("regular", True)],
)
def test_apply_quarantines_and_restores_last_moment_source_substitution(
    tmp_path: Path,
    db_session: Session,
    replacement_kind: str,
    block_compensation: bool,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    original = source.read_bytes()
    displaced_original = library / "external-original.mp3"
    foreign_bytes = b"foreign entry moved intact"
    entrant_bytes = b"later entrant must not be clobbered"
    sentinel = tmp_path / "external-sentinel"
    sentinel.write_bytes(b"sentinel remains unchanged")
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path, db_session=db_session, path=source, new_title="Changed"
    )
    from muzilla.changes import writer

    real_rename = writer._rename_entry_no_replace
    substituted = False
    blocked = False

    def substitute_at_syscall(
        source_fd: int, source_name: str, destination_fd: int, destination_name: str
    ) -> None:
        nonlocal substituted, blocked
        if not substituted and source_name == source.name and destination_name == "original":
            source.rename(displaced_original)
            if replacement_kind == "symlink":
                source.symlink_to(sentinel)
            else:
                source.write_bytes(foreign_bytes)
            substituted = True
        if (
            block_compensation
            and substituted
            and not blocked
            and source_name == "original"
            and destination_name == source.name
        ):
            source.write_bytes(entrant_bytes)
            blocked = True
        return real_rename(source_fd, source_name, destination_fd, destination_name)

    with patch.object(writer, "_rename_entry_no_replace", side_effect=substitute_at_syscall):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=BlobStore(tmp_path / "blobs"),
        )

    assert substituted
    assert result.state == "failed"
    assert result.recovery_required is block_compensation
    assert displaced_original.read_bytes() == original
    assert sentinel.read_bytes() == b"sentinel remains unchanged"
    if replacement_kind == "symlink":
        assert source.is_symlink() and source.resolve() == sentinel
    else:
        assert source.read_bytes() == (entrant_bytes if block_compensation else foreign_bytes)
    db_session.refresh(track)
    assert track.title != "Changed"

    private_root = library / ".muzilla-private"
    if block_compensation:
        operation_dirs = list(private_root.iterdir())
        assert len(operation_dirs) == 1
        assert {entry.name for entry in operation_dirs[0].iterdir()} == {"original", "staged"}
        assert (operation_dirs[0] / "original").read_bytes() == foreign_bytes
    else:
        assert list(private_root.iterdir()) == []


def test_apply_preserves_quarantined_original_when_publication_name_is_occupied(
    tmp_path: Path, db_session: Session
) -> None:
    from sqlalchemy import select

    from muzilla.changes import writer
    from muzilla.changes.bundle_applier import recover_apply_runs
    from muzilla.db.models import ApplyRun, ReviewFileJournal

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    original = source.read_bytes()
    entrant = b"published-name entrant must survive"
    displaced = library / "foreign-entrant.mp3"
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path, db_session=db_session, path=source, new_title="Changed"
    )
    real_rename = writer._rename_entry_no_replace
    planted = False

    def occupy_before_publish(
        source_fd: int, source_name: str, destination_fd: int, destination_name: str
    ) -> None:
        nonlocal planted
        if not planted and source_name == "staged" and destination_name == source.name:
            source.write_bytes(entrant)
            planted = True
        return real_rename(source_fd, source_name, destination_fd, destination_name)

    with patch.object(writer, "_rename_entry_no_replace", side_effect=occupy_before_publish):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=BlobStore(tmp_path / "blobs"),
        )

    assert planted
    assert result.state == "failed" and result.recovery_required
    assert source.read_bytes() == entrant
    private_root = library / ".muzilla-private"
    operation_dir = next(private_root.iterdir())
    assert {entry.name for entry in operation_dir.iterdir()} == {"original", "staged"}
    assert (operation_dir / "original").read_bytes() == original
    journal = db_session.scalar(
        select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == apply_run_id)
    )
    assert journal is not None and journal.state == "writing"
    transition = journal.before_blob["__muzilla_publication_transition"]
    assert isinstance(transition, dict)
    private_identity = transition["private_root_identity"]
    assert isinstance(private_identity, dict)
    assert transition["phase"] == "compensation_blocked"
    assert private_identity["inode"] == private_root.stat().st_ino
    run = db_session.get(ApplyRun, apply_run_id)
    assert run is not None and run.result is not None
    assert run.result["recovery_required"] is True
    db_session.refresh(track)
    assert track.title != "Changed"

    assert (
        recover_apply_runs(
            db_session, library_root=library, blob_store=BlobStore(tmp_path / "blobs")
        )
        == 1
    )
    assert source.read_bytes() == entrant
    assert (operation_dir / "original").read_bytes() == original
    source.rename(displaced)
    assert (
        recover_apply_runs(
            db_session, library_root=library, blob_store=BlobStore(tmp_path / "blobs")
        )
        == 1
    )
    assert source.read_bytes() == original
    assert displaced.read_bytes() == entrant
    assert list(private_root.iterdir()) == []
    db_session.refresh(track)
    assert track.title != "Changed"
    run = db_session.get(ApplyRun, apply_run_id)
    assert run is not None and run.result is not None
    assert run.result["recovery_required"] is False


def test_apply_cleanup_does_not_remove_substituted_private_anchor_or_public_temp(
    tmp_path: Path, db_session: Session
) -> None:
    from muzilla.changes import writer

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    sentinel_dir = tmp_path / "sentinel-dir"
    sentinel_dir.mkdir()
    sentinel = sentinel_dir / "do-not-follow"
    sentinel.write_bytes(b"sentinel is immutable")
    displaced_private_root = library / ".muzilla-private-original"
    temporary = source.with_name(source.name + ".muzilla.tmp")
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path, db_session=db_session, path=source, new_title="Changed"
    )
    real_finalize = writer.finalize_publication_transition

    def substitute_before_cleanup(
        path: Path, transition: dict[str, object], *, library_root: Path | None
    ) -> bool:
        private_root = library / ".muzilla-private"
        private_root.rename(displaced_private_root)
        private_root.symlink_to(sentinel_dir, target_is_directory=True)
        temporary.symlink_to(sentinel)
        return real_finalize(path, transition, library_root=library_root)

    with patch.object(
        writer, "finalize_publication_transition", side_effect=substitute_before_cleanup
    ):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=BlobStore(tmp_path / "blobs"),
        )

    assert result.state == "applied"
    assert temporary.is_symlink() and temporary.resolve() == sentinel
    assert sentinel.read_bytes() == b"sentinel is immutable"
    assert (library / ".muzilla-private").is_symlink()
    assert (library / ".muzilla-private").resolve() == sentinel_dir
    assert displaced_private_root.is_dir()
    operation_dir = next(displaced_private_root.iterdir())
    assert (operation_dir / "original").is_file()
    db_session.refresh(track)
    assert track.title == "Changed"


def test_apply_rejects_ancestor_symlink_substitution_during_tag_write(
    tmp_path: Path, db_session: Session
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    original_source = source.read_bytes()
    relocated_library = tmp_path / "relocated-library"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "song.mp3"
    sentinel.write_bytes(b"outside sentinel")
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path, db_session=db_session, path=source, new_title="Changed"
    )

    from muzilla.tags.writer import write_fields as real_write_fields

    def replace_library_root(filething: Any, field_values: dict[str, Any]) -> None:
        real_write_fields(filething, field_values)
        library.rename(relocated_library)
        library.symlink_to(outside, target_is_directory=True)

    with patch("muzilla.changes.writer.write_fields", side_effect=replace_library_root):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=BlobStore(tmp_path / "blobs"),
        )

    assert result.state == "failed"
    assert sentinel.read_bytes() == b"outside sentinel"
    assert (relocated_library / "song.mp3").read_bytes() == original_source
    assert not list(relocated_library.glob(".song.mp3.muzilla-*.tmp"))
    db_session.refresh(track)
    assert track.title != "Changed"


def test_tag_writer_saves_through_pinned_filething_for_supported_formats(
    tmp_path: Path,
) -> None:
    from muzilla.changes.writer import capture_file_guard
    from muzilla.tags.writer import write_fields

    for filename in ("silence.mp3", "silence.flac", "silence.m4a", "silence.ogg", "silence.opus"):
        target = tmp_path / filename
        shutil.copy2(Path("tests/fixtures/audio") / filename, target)
        with _StagedReplacement(
            target, tmp_path, expected_guard=capture_file_guard(target, tmp_path)
        ) as staged:
            write_fields(staged.filething, {"title": "Descriptor-backed"})
            staged.publish(tmp_path)
            assert read_track(target).title == "Descriptor-backed", filename
            staged.finalize_after_durable_outcome()
        assert read_track(target).title == "Descriptor-backed", filename


@pytest.mark.skipif(sys.platform != "linux", reason="Linux relatime and O_NOATIME contract")
def test_restore_preserves_old_atime_across_repeated_staged_guard_reads(
    tmp_path: Path, db_session: Session
) -> None:
    assert fcntl is not None
    fcntl_module = fcntl
    source = tmp_path / "restore-atime.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    preserved_time_ns = 1_234_567_890_123_456_000
    os.utime(source, ns=(preserved_time_ns, preserved_time_ns))

    probe = tmp_path / "relatime-probe"
    probe.write_bytes(b"probe")
    os.utime(probe, ns=(preserved_time_ns, preserved_time_ns))
    probe_fd = os.open(probe, os.O_RDONLY)
    try:
        before_read = os.fstat(probe_fd)
        os.pread(probe_fd, 1, 0)
        after_read = os.fstat(probe_fd)
    finally:
        os.close(probe_fd)
    assert before_read.st_atime_ns == preserved_time_ns
    assert after_read.st_atime_ns > before_read.st_atime_ns

    real_capture = _StagedReplacement.capture_published_guard
    repeated_reads = 0

    def capture_repeatedly(
        staged: _StagedReplacement, library_root: Path | None
    ) -> dict[str, object]:
        nonlocal repeated_reads
        guard = real_capture(staged, library_root)
        assert staged.temp_fd is not None
        assert staged.fileobj is not None
        noatime_flag = getattr(os, "O_NOATIME", None)
        assert noatime_flag is not None
        assert fcntl_module.fcntl(staged.fileobj.fileno(), fcntl_module.F_GETFL) & noatime_flag
        for _ in range(3):
            assert real_capture(staged, library_root) == guard
            assert fcntl_module.fcntl(staged.temp_fd, fcntl_module.F_GETFL) & noatime_flag
            assert os.fstat(staged.temp_fd).st_atime_ns == preserved_time_ns
            repeated_reads += 1
        return guard

    with patch.object(_StagedReplacement, "capture_published_guard", capture_repeatedly):
        restored_guard = restore_from_before_blob(
            db_session,
            source,
            {"title": "Atime guard regression"},
            blob_store=None,
            library_root=tmp_path,
        )

    assert repeated_reads >= 6
    assert restored_guard["file"]
    restored_stat = source.stat()
    assert restored_stat.st_atime_ns == preserved_time_ns
    assert restored_stat.st_mtime_ns == preserved_time_ns


@pytest.mark.skipif(sys.platform != "linux", reason="Linux O_NOATIME setup failure contract")
def test_noatime_setup_failure_cleans_owned_stage_and_preserves_source_and_catalog(
    tmp_path: Path, db_session: Session
) -> None:
    assert fcntl is not None
    fcntl_module = fcntl
    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    store = BlobStore(tmp_path / "blobs")
    catalog_blob = store.put(db_session, b"catalog art remains intact", mime="image/jpeg")
    db_session.commit()
    assert catalog_blob.id is not None
    track, _ = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Would change after publication",
        catalog_art_blob_id=catalog_blob.id,
    )
    original_stat = source.stat()
    original_bytes = source.read_bytes()
    original_title = track.title
    foreign_stage_bytes = b"foreign staging entry must survive cleanup"
    real_fcntl = fcntl_module.fcntl

    def fail_noatime_setup(file_fd: int, command: int, *args: int) -> int:
        if command == fcntl_module.F_SETFL:
            private_root = library / ".muzilla-private"
            operation_dirs = list(private_root.iterdir())
            assert len(operation_dirs) == 1
            (operation_dirs[0] / "foreign-stage").write_bytes(foreign_stage_bytes)
            raise OSError(errno.EPERM, "injected O_NOATIME setup failure")
        return real_fcntl(file_fd, command, *args)

    with (
        patch("fcntl.fcntl", side_effect=fail_noatime_setup),
        pytest.raises(OSError, match="injected O_NOATIME setup failure"),
    ):
        restore_from_before_blob(
            db_session,
            source,
            {"title": "Must not be published"},
            blob_store=store,
            library_root=library,
        )

    current_stat = source.stat()
    assert (current_stat.st_dev, current_stat.st_ino) == (
        original_stat.st_dev,
        original_stat.st_ino,
    )
    assert source.read_bytes() == original_bytes
    db_session.refresh(track)
    assert track.title == original_title
    assert track.art_blob_id == catalog_blob.id
    assert store.get_bytes(catalog_blob) == b"catalog art remains intact"
    operation_dirs = list((library / ".muzilla-private").iterdir())
    assert len(operation_dirs) == 1
    assert [entry.name for entry in operation_dirs[0].iterdir()] == ["foreign-stage"]
    assert (operation_dirs[0] / "foreign-stage").read_bytes() == foreign_stage_bytes


def test_apply_does_not_mutate_music_when_art_capture_fails(
    tmp_path: Path, db_session: Session
) -> None:
    from mutagen.id3 import APIC, ID3

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    tags = ID3(str(source))  # type: ignore[no-untyped-call]
    tags.add(
        APIC(encoding=3, mime="image/jpeg", type=3, desc="front", data=b"cover bytes")  # type: ignore[no-untyped-call]
    )
    tags.save(str(source))
    original_source = source.read_bytes()
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        remove_art=True,
    )

    with patch(
        "muzilla.changes.writer.capture_embedded_art",
        side_effect=RuntimeError("art capture fault"),
    ):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=BlobStore(tmp_path / "blobs"),
        )

    assert result.state == "failed"
    assert not result.recovery_required
    assert "art capture fault" in (result.errors.get(track.id) or "")
    assert source.read_bytes() == original_source
    assert ID3(str(source)).getall("APIC")  # type: ignore[no-untyped-call]
    db_session.refresh(track)
    assert track.title != "Changed"


def test_apply_does_not_mutate_music_when_inverse_blob_storage_fails(
    tmp_path: Path, db_session: Session
) -> None:
    from mutagen.id3 import APIC, ID3

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    tags = ID3(str(source))  # type: ignore[no-untyped-call]
    tags.add(
        APIC(encoding=3, mime="image/jpeg", type=3, desc="front", data=b"cover bytes")  # type: ignore[no-untyped-call]
    )
    tags.save(str(source))
    original_source = source.read_bytes()
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        remove_art=True,
    )
    store = BlobStore(tmp_path / "blobs")

    with patch.object(store, "put", side_effect=OSError("blob fsync fault")):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=store,
        )

    assert result.state == "failed"
    assert not result.recovery_required
    assert "blob fsync fault" in (result.errors.get(track.id) or "")
    assert source.read_bytes() == original_source
    assert ID3(str(source)).getall("APIC")  # type: ignore[no-untyped-call]
    db_session.refresh(track)
    assert track.title != "Changed"


@pytest.mark.parametrize("reuse_kind", ["database-row", "orphan"])
def test_apply_does_not_mutate_music_when_reused_inverse_blob_fsync_fails(
    tmp_path: Path, db_session: Session, reuse_kind: str
) -> None:
    from mutagen.id3 import APIC, ID3

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    tags = ID3(str(source))  # type: ignore[no-untyped-call]
    tags.add(
        APIC(encoding=3, mime="image/jpeg", type=3, desc="front", data=b"cover bytes")  # type: ignore[no-untyped-call]
    )
    tags.save(str(source))
    original_source = source.read_bytes()
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        remove_art=True,
    )
    store = BlobStore(tmp_path / "blobs")
    blob = store.put(db_session, b"cover bytes", mime="image/jpeg")
    db_session.commit()
    blob_path = store.get_path(blob)
    if reuse_kind == "orphan":
        db_session.delete(blob)
        db_session.commit()
    blob_stat = blob_path.stat()
    real_fsync = os.fsync

    def fail_reused_blob_fsync(file_fd: int) -> None:
        actual = os.fstat(file_fd)
        if (actual.st_dev, actual.st_ino) == (blob_stat.st_dev, blob_stat.st_ino):
            raise OSError("reused inverse blob fsync fault")
        real_fsync(file_fd)

    with patch("muzilla.changes.blobstore.os.fsync", side_effect=fail_reused_blob_fsync):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=store,
        )

    assert result.state == "failed"
    assert "reused inverse blob fsync fault" in (result.errors.get(track.id) or "")
    assert source.read_bytes() == original_source
    assert ID3(str(source)).getall("APIC")  # type: ignore[no-untyped-call]
    db_session.refresh(track)
    assert track.title != "Changed"


def _prepare_catalog_art_inverse(
    tmp_path: Path, db_session: Session
) -> tuple[Path, bytes, bytes, bytes, Track, int, BlobStore, int, Path]:
    from mutagen.id3 import APIC, ID3

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    embedded_bytes = b"embedded artwork bytes"
    catalog_bytes = b"different catalog artwork bytes"
    tags = ID3(str(source))  # type: ignore[no-untyped-call]
    tags.add(
        APIC(encoding=3, mime="image/jpeg", type=3, desc="front", data=embedded_bytes)  # type: ignore[no-untyped-call]
    )
    tags.save(str(source))
    original_source = source.read_bytes()
    store = BlobStore(tmp_path / "blobs")
    catalog_blob = store.put(db_session, catalog_bytes, mime="image/jpeg")
    db_session.commit()
    assert catalog_blob.id is not None
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        remove_art=True,
        catalog_art_blob_id=catalog_blob.id,
    )
    return (
        source,
        original_source,
        embedded_bytes,
        catalog_bytes,
        track,
        apply_run_id,
        store,
        catalog_blob.id,
        store.get_path(catalog_blob),
    )


def test_apply_does_not_mutate_music_when_catalog_art_fsync_fails(
    tmp_path: Path, db_session: Session
) -> None:
    from mutagen.id3 import ID3

    (
        source,
        original_source,
        embedded_bytes,
        catalog_bytes,
        track,
        apply_run_id,
        store,
        _blob_id,
        blob_path,
    ) = _prepare_catalog_art_inverse(tmp_path, db_session)
    assert catalog_bytes != embedded_bytes
    blob_stat = blob_path.stat()
    real_fsync = os.fsync

    def fail_catalog_art_fsync(file_fd: int) -> None:
        actual = os.fstat(file_fd)
        if (actual.st_dev, actual.st_ino) == (blob_stat.st_dev, blob_stat.st_ino):
            raise OSError("catalog artwork fsync fault")
        real_fsync(file_fd)

    with patch("muzilla.changes.blobstore.os.fsync", side_effect=fail_catalog_art_fsync):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=source.parent,
            blob_store=store,
        )

    assert result.state == "failed"
    assert "catalog artwork fsync fault" in (result.errors.get(track.id) or "")
    assert source.read_bytes() == original_source
    assert [image.data for image in ID3(str(source)).getall("APIC")] == [embedded_bytes]  # type: ignore[no-untyped-call]
    db_session.refresh(track)
    assert track.title != "Changed"


def test_apply_fsyncs_catalog_art_before_retain_and_journal_checkpoint(
    tmp_path: Path, db_session: Session
) -> None:
    from muzilla.db.models import Blob, ReviewFileJournal

    (
        source,
        _original_source,
        embedded_bytes,
        catalog_bytes,
        _track,
        apply_run_id,
        store,
        catalog_blob_id,
        blob_path,
    ) = _prepare_catalog_art_inverse(tmp_path, db_session)
    catalog_blob = store.get_by_id(db_session, catalog_blob_id)
    assert catalog_blob is not None
    assert store.get_bytes(catalog_blob) == catalog_bytes
    assert catalog_bytes != embedded_bytes
    blob_stat = blob_path.stat()
    directory_paths = {
        "blob-root": store.root,
        "first-shard": blob_path.parent.parent,
        "blob-directory": blob_path.parent,
    }
    directory_stats = {name: path.stat() for name, path in directory_paths.items()}
    events: list[str] = []
    real_fsync = os.fsync
    real_retain = store.retain
    real_commit = db_session.commit

    def record_fsync(file_fd: int) -> None:
        actual = os.fstat(file_fd)
        if (actual.st_dev, actual.st_ino) == (blob_stat.st_dev, blob_stat.st_ino):
            event = "catalog-file"
        else:
            event = next(
                (
                    name
                    for name, expected in directory_stats.items()
                    if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino)
                ),
                "",
            )
        real_fsync(file_fd)
        if event:
            events.append(event)

    def record_retain(session: Session, blob: Blob) -> None:
        if blob.id == catalog_blob_id:
            events.append("catalog-retain")
        real_retain(session, blob)

    def record_commit() -> None:
        for pending in [*db_session.new, *db_session.dirty]:
            if not isinstance(pending, ReviewFileJournal) or pending.phase != "tags":
                continue
            before = pending.before_blob
            artwork = before.get("__muzilla_embedded_art") if isinstance(before, dict) else None
            if (
                isinstance(before, dict)
                and before.get("__muzilla_catalog_art_blob_id") == catalog_blob_id
                and isinstance(artwork, dict)
                and artwork.get("entries")
            ):
                events.append("journal-checkpoint")
                break
        real_commit()

    with (
        patch("muzilla.changes.blobstore.os.fsync", side_effect=record_fsync),
        patch.object(store, "retain", side_effect=record_retain),
        patch.object(db_session, "commit", side_effect=record_commit),
    ):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=source.parent,
            blob_store=store,
        )

    assert result.state == "applied"
    catalog_retain = events.index("catalog-retain")
    checkpoint = events.index("journal-checkpoint")
    assert events.index("catalog-file") < catalog_retain
    assert {
        "blob-root",
        "first-shard",
        "blob-directory",
    } <= set(events[:catalog_retain])
    assert catalog_retain < checkpoint


def test_apply_fsyncs_reused_inverse_blob_before_journal_checkpoint(
    tmp_path: Path, db_session: Session
) -> None:
    from mutagen.id3 import APIC, ID3

    from muzilla.db.models import ReviewFileJournal

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    tags = ID3(str(source))  # type: ignore[no-untyped-call]
    tags.add(
        APIC(encoding=3, mime="image/jpeg", type=3, desc="front", data=b"cover bytes")  # type: ignore[no-untyped-call]
    )
    tags.save(str(source))
    _track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        remove_art=True,
    )
    store = BlobStore(tmp_path / "blobs")
    blob = store.put(db_session, b"cover bytes", mime="image/jpeg")
    db_session.commit()
    blob_path = store.get_path(blob)
    blob_stat = blob_path.stat()
    directory_paths = {
        "blob-root": store.root,
        "first-shard": blob_path.parent.parent,
        "blob-directory": blob_path.parent,
    }
    directory_stats = {name: path.stat() for name, path in directory_paths.items()}
    events: list[str] = []
    real_fsync = os.fsync
    real_commit = db_session.commit

    def record_fsync(file_fd: int) -> None:
        actual = os.fstat(file_fd)
        if (actual.st_dev, actual.st_ino) == (blob_stat.st_dev, blob_stat.st_ino):
            events.append("blob-file")
        else:
            for name, expected in directory_stats.items():
                if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino):
                    events.append(name)
                    break
        real_fsync(file_fd)

    def record_commit() -> None:
        for pending in [*db_session.new, *db_session.dirty]:
            if not isinstance(pending, ReviewFileJournal) or pending.phase != "tags":
                continue
            before = pending.before_blob
            artwork = before.get("__muzilla_embedded_art") if isinstance(before, dict) else None
            if isinstance(artwork, dict) and artwork.get("entries"):
                events.append("journal-checkpoint")
                break
        real_commit()

    with (
        patch("muzilla.changes.blobstore.os.fsync", side_effect=record_fsync),
        patch.object(db_session, "commit", side_effect=record_commit),
    ):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=store,
        )

    assert result.state == "applied"
    checkpoint = events.index("journal-checkpoint")
    assert "blob-file" in events[:checkpoint]
    assert {"blob-root", "first-shard", "blob-directory"} <= set(events[:checkpoint])


def test_undo_restore_rejects_planted_temp_symlink(tmp_path: Path, db_session: Session) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"must remain unchanged")
    temporary = source.with_name(source.name + ".muzilla.tmp")
    temporary.symlink_to(sentinel)
    original_source = source.read_bytes()

    with pytest.raises(FileExistsError, match="reserved temporary"):
        restore_from_before_blob(
            db_session,
            source,
            {"title": "restored"},
            blob_store=None,
            library_root=library,
        )

    assert source.read_bytes() == original_source
    assert sentinel.read_bytes() == b"must remain unchanged"
    assert temporary.is_symlink()


def _picture_block(mime: str, description: str, data: bytes, image_type: int) -> Any:
    from mutagen.flac import Picture

    picture = Picture()  # type: ignore[no-untyped-call]
    picture.type = image_type
    picture.mime = mime
    picture.desc = description
    picture.width = 320 if image_type == 3 else 160
    picture.height = 240 if image_type == 3 else 120
    picture.depth = 24 if image_type == 3 else 32
    picture.colors = 5 if image_type == 3 else 9
    picture.data = data
    return picture


def _add_native_art_collection(path: Path) -> None:
    from mutagen.flac import FLAC
    from mutagen.id3 import APIC, ID3, Encoding
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis

    if path.suffix == ".mp3":
        tags = ID3(str(path))  # type: ignore[no-untyped-call]
        tags.add(
            APIC(encoding=Encoding.LATIN1, mime="image/jpeg", type=3, desc="front", data=b"front")  # type: ignore[no-untyped-call]
        )
        tags.add(
            APIC(encoding=Encoding.UTF16, mime="image/png", type=4, desc="back", data=b"back")  # type: ignore[no-untyped-call]
        )
        tags.save(str(path))
    elif path.suffix == ".flac":
        flac_audio = FLAC(str(path))  # type: ignore[no-untyped-call]
        flac_audio.add_picture(_picture_block("image/jpeg", "front", b"front", 3))  # type: ignore[no-untyped-call]
        flac_audio.add_picture(_picture_block("image/png", "back", b"back", 4))  # type: ignore[no-untyped-call]
        flac_audio.save()
    elif path.suffix == ".m4a":
        mp4_audio = MP4(str(path))  # type: ignore[no-untyped-call]
        if mp4_audio.tags is None:
            mp4_audio.add_tags()  # type: ignore[no-untyped-call]
        assert mp4_audio.tags is not None
        mp4_audio.tags["covr"] = [
            MP4Cover(b"front", imageformat=MP4Cover.FORMAT_JPEG),
            MP4Cover(b"back", imageformat=MP4Cover.FORMAT_PNG),
        ]
        mp4_audio.save()
    elif path.suffix in {".ogg", ".opus"}:
        ogg_audio = (
            OggOpus(str(path))  # type: ignore[no-untyped-call]
            if path.suffix == ".opus"
            else OggVorbis(str(path))  # type: ignore[no-untyped-call]
        )
        assert ogg_audio.tags is not None
        import base64

        ogg_audio.tags["metadata_block_picture"] = [
            base64.b64encode(_picture_block("image/jpeg", "front", b"front", 3).write()).decode(
                "ascii"
            ),
            base64.b64encode(_picture_block("image/png", "back", b"back", 4).write()).decode(
                "ascii"
            ),
        ]
        ogg_audio.save()
    else:
        raise AssertionError(f"unsupported test fixture: {path.suffix}")


@pytest.mark.parametrize(
    "drift",
    ["artwork", "middle-audio", "inode", "missing", "symlink-ancestry"],
)
def test_undo_fails_closed_on_physical_file_drift(
    tmp_path: Path, db_session: Session, drift: str
) -> None:
    from mutagen.id3 import APIC, ID3

    from muzilla.changes.bundle_undo import apply_review_undo_run
    from muzilla.services.review_undo import enqueue_review_undo

    library = tmp_path / "library"
    library.mkdir()
    parent = library / "nested" if drift == "symlink-ancestry" else library
    if parent != library:
        parent.mkdir()
    source = parent / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    _add_native_art_collection(source)
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        remove_art=True,
    )
    store = BlobStore(tmp_path / "blobs")
    applied = apply_review_run(db_session, apply_run_id, library_root=library, blob_store=store)
    assert applied.state == "applied"
    applied_bytes = source.read_bytes()
    applied_inode = source.stat().st_ino
    outside_sentinel: Path | None = None
    displaced_directory: Path | None = None

    if drift == "artwork":
        tags = ID3(str(source))  # type: ignore[no-untyped-call]
        tags.add(
            APIC(
                encoding=3,
                mime="image/png",
                type=4,
                desc="external-back-cover",
                data=b"external artwork bytes",
            )  # type: ignore[no-untyped-call]
        )
        tags.save(str(source))
    elif drift == "middle-audio":
        content = bytearray(source.read_bytes())
        content[len(content) // 2] ^= 1
        source.write_bytes(content)
    elif drift == "inode":
        replacement = source.with_name("replacement.mp3")
        shutil.copy2(source, replacement)
        os.replace(replacement, source)
        assert source.stat().st_ino != applied_inode
    elif drift == "missing":
        source.unlink()
    else:
        displaced_directory = library / "original-directory"
        parent.rename(displaced_directory)
        outside = tmp_path / "outside"
        outside.mkdir()
        outside_sentinel = outside / "song.mp3"
        outside_sentinel.write_bytes(b"outside sentinel")
        parent.symlink_to(outside, target_is_directory=True)

    preserved_bytes = source.read_bytes() if drift not in {"missing", "symlink-ancestry"} else None
    undo = enqueue_review_undo(
        db_session,
        applied.review_bundle_id,
        apply_run_id=apply_run_id,
        idempotency_key=f"physical-drift-{drift}",
        backup=False,
    )
    db_session.commit()
    result = apply_review_undo_run(
        db_session,
        undo.undo_run_id,
        library_root=library,
        blob_store=store,
    )

    assert result.state == "failed"
    assert result.recovery_required
    if drift == "missing":
        assert not source.exists()
    elif drift == "symlink-ancestry":
        assert outside_sentinel is not None
        assert outside_sentinel.read_bytes() == b"outside sentinel"
        assert displaced_directory is not None
        assert (displaced_directory / "song.mp3").read_bytes() == applied_bytes
    else:
        assert preserved_bytes is not None
        assert source.read_bytes() == preserved_bytes
        if drift == "inode":
            assert source.stat().st_ino != applied_inode
        if drift == "artwork":
            from muzilla.tags.writer import capture_embedded_art

            art = capture_embedded_art(source)
            assert [entry["data"] for entry in art["entries"]] == [b"external artwork bytes"]
    db_session.refresh(track)
    assert track.title == "Changed"


def test_undo_enqueue_fails_closed_when_tag_history_is_missing(
    tmp_path: Path, db_session: Session
) -> None:
    from sqlalchemy import select

    from muzilla.db.models import ReviewFileJournal
    from muzilla.services.review_undo import ReviewUndoError, enqueue_review_undo

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    original = source.read_bytes()
    applied, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path, db_session=db_session, path=source, new_title="Changed"
    )
    store = BlobStore(tmp_path / "blobs")
    result = apply_review_run(db_session, apply_run_id, library_root=library, blob_store=store)
    assert result.state == "applied"
    journal = db_session.scalar(
        select(ReviewFileJournal).where(
            ReviewFileJournal.apply_run_id == apply_run_id,
            ReviewFileJournal.track_id == applied.id,
            ReviewFileJournal.phase == "tags",
        )
    )
    assert journal is not None
    journal.before_blob = {
        key: value
        for key, value in journal.before_blob.items()
        if not key.startswith("__muzilla_physical_guard_")
    }
    db_session.commit()
    with pytest.raises(ReviewUndoError, match="source tag inverse is incomplete"):
        enqueue_review_undo(
            db_session,
            result.review_bundle_id,
            apply_run_id=apply_run_id,
            idempotency_key="missing-physical-history",
            backup=False,
        )

    assert source.read_bytes() != original
    db_session.refresh(applied)
    assert applied.title == "Changed"


def test_undo_enqueue_fails_closed_when_move_history_is_missing(
    tmp_path: Path, db_session: Session
) -> None:
    from sqlalchemy import select

    from muzilla.db.models import ReviewFileJournal
    from muzilla.services.review_undo import ReviewUndoError, enqueue_review_undo

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    moved = library / "song.mp3.renamed"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        move_after_tags=True,
    )
    store = BlobStore(tmp_path / "blobs")
    applied = apply_review_run(db_session, apply_run_id, library_root=library, blob_store=store)
    assert applied.state == "applied"
    move_journal = db_session.scalar(
        select(ReviewFileJournal).where(
            ReviewFileJournal.apply_run_id == apply_run_id,
            ReviewFileJournal.track_id == track.id,
            ReviewFileJournal.phase == "move",
        )
    )
    assert move_journal is not None
    before_blob = dict(move_journal.before_blob or {})
    assert "__muzilla_physical_guard_after" in before_blob
    before_blob.pop("__muzilla_physical_guard_after")
    move_journal.before_blob = before_blob
    db_session.commit()

    applied_bytes = moved.read_bytes()
    with pytest.raises(ReviewUndoError, match="source move inverse is incomplete"):
        enqueue_review_undo(
            db_session,
            applied.review_bundle_id,
            apply_run_id=apply_run_id,
            idempotency_key="missing-move-physical-history",
            backup=False,
        )

    assert not source.exists()
    assert moved.read_bytes() == applied_bytes
    db_session.refresh(track)
    assert track.path == str(moved)


def test_undo_preserves_drift_after_tagged_file_was_moved(
    tmp_path: Path, db_session: Session
) -> None:
    from muzilla.changes.bundle_undo import apply_review_undo_run
    from muzilla.services.review_undo import enqueue_review_undo

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    moved = library / "song.mp3.renamed"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        move_after_tags=True,
    )
    store = BlobStore(tmp_path / "blobs")
    applied = apply_review_run(db_session, apply_run_id, library_root=library, blob_store=store)
    assert applied.state == "applied"
    content = bytearray(moved.read_bytes())
    content[len(content) // 2] ^= 1
    moved.write_bytes(content)
    external_bytes = moved.read_bytes()
    undo = enqueue_review_undo(
        db_session,
        applied.review_bundle_id,
        apply_run_id=apply_run_id,
        idempotency_key="moved-file-physical-drift",
        backup=False,
    )
    db_session.commit()

    undo_result = apply_review_undo_run(
        db_session, undo.undo_run_id, library_root=library, blob_store=store
    )

    assert undo_result.state == "failed"
    assert undo_result.recovery_required
    assert moved.read_bytes() == external_bytes
    assert not source.exists()
    db_session.refresh(track)
    assert track.path == str(moved)


def test_rollback_fails_closed_on_external_artwork_drift(
    tmp_path: Path, db_session: Session
) -> None:
    from mutagen.id3 import APIC, ID3

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    _add_native_art_collection(source)
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        remove_art=True,
        move_after_tags=True,
    )
    external_bytes: bytes | None = None

    def fail_move_after_external_artwork(*_args: Any, **_kwargs: Any) -> tuple[bool, str]:
        nonlocal external_bytes
        tags = ID3(str(source))  # type: ignore[no-untyped-call]
        tags.add(
            APIC(
                encoding=3,
                mime="image/png",
                type=4,
                desc="external-during-rollback",
                data=b"external rollback artwork",
            )  # type: ignore[no-untyped-call]
        )
        tags.save(str(source))
        external_bytes = source.read_bytes()
        return False, "injected move failure after external edit"

    with patch(
        "muzilla.changes.bundle_applier.write_move",
        side_effect=fail_move_after_external_artwork,
    ):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=BlobStore(tmp_path / "blobs"),
        )

    assert result.state == "failed"
    assert result.recovery_required
    assert external_bytes is not None and source.read_bytes() == external_bytes
    assert [
        entry["data"]
        for entry in __import__(
            "muzilla.tags.writer", fromlist=["capture_embedded_art"]
        ).capture_embedded_art(source)["entries"]
    ] == [b"external rollback artwork"]
    db_session.refresh(track)
    assert track.title == "Changed"


@pytest.mark.parametrize("outcome", ["undo", "rollback"])
def test_case_only_move_apply_undo_and_rollback_preserve_exact_spelling(
    tmp_path: Path, db_session: Session, outcome: str
) -> None:
    from muzilla.changes.bundle_undo import apply_review_undo_run
    from muzilla.services.review_undo import enqueue_review_undo

    library = tmp_path / "library"
    library.mkdir()
    source = library / "Track.mp3"
    destination = library / "track.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        move_after_tags=True,
        move_destination=destination.name,
    )
    store = BlobStore(tmp_path / "blobs")

    if outcome == "rollback":
        from muzilla.changes.writer import write_move as real_write_move

        def move_then_fail(*args: Any, **kwargs: Any) -> tuple[bool, str | None]:
            ok, error = real_write_move(*args, **kwargs)
            assert ok, error
            return False, "injected failure after case-only move"

        with patch("muzilla.changes.bundle_applier.write_move", side_effect=move_then_fail):
            result = apply_review_run(
                db_session, apply_run_id, library_root=library, blob_store=store
            )
        assert result.state == "failed"
        assert not result.recovery_required
        assert _library_entry_names(library) == [source.name]
        db_session.refresh(track)
        assert track.path == str(source)
        assert track.title != "Changed"
        return

    applied = apply_review_run(db_session, apply_run_id, library_root=library, blob_store=store)
    assert applied.state == "applied", applied
    assert _library_entry_names(library) == [destination.name]
    db_session.refresh(track)
    assert track.path == str(destination)

    undo = enqueue_review_undo(
        db_session,
        applied.review_bundle_id,
        apply_run_id=apply_run_id,
        idempotency_key="case-only-move-undo",
        backup=False,
    )
    db_session.commit()
    undone = apply_review_undo_run(
        db_session, undo.undo_run_id, library_root=library, blob_store=store
    )

    assert undone.state == "undone", undone
    assert _library_entry_names(library) == [source.name]
    db_session.refresh(track)
    assert track.path == str(source)
    assert track.title != "Changed"


def test_move_no_clobber_allows_case_alias_entry_but_not_hard_link_alias(
    tmp_path: Path,
) -> None:
    from muzilla.changes.writer import _move_no_clobber

    source = tmp_path / "Track.mp3"
    destination = tmp_path / "track.mp3"
    source.write_bytes(b"case-only source")
    if not destination.exists() or not destination.samefile(source):
        pytest.skip("case-only directory-entry alias requires a case-insensitive filesystem")
    assert [entry.name for entry in tmp_path.iterdir()] == [source.name]

    checkpoints: list[dict[str, object]] = []
    _move_no_clobber(
        source,
        destination,
        same_file=True,
        checkpoint=checkpoints.append,
    )

    assert [entry.name for entry in tmp_path.iterdir()] == [destination.name]
    assert destination.read_bytes() == b"case-only source"
    assert checkpoints == [] or [entry["step"] for entry in checkpoints] == [
        "first_intent",
        "first_done",
        "final_intent",
        "final_done",
    ]


def test_undo_cancellation_waits_for_complete_file_inverse(
    tmp_path: Path, db_session: Session
) -> None:
    from sqlalchemy import select

    from muzilla.changes.bundle_undo import apply_review_undo_run
    from muzilla.db.models import ReviewFileJournal
    from muzilla.services.review_undo import enqueue_review_undo

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    moved = library / "song.mp3.renamed"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        move_after_tags=True,
    )
    store = BlobStore(tmp_path / "blobs")
    applied = apply_review_run(db_session, apply_run_id, library_root=library, blob_store=store)
    assert applied.state == "applied"
    undo = enqueue_review_undo(
        db_session,
        applied.review_bundle_id,
        apply_run_id=apply_run_id,
        idempotency_key="cancel-only-between-file-inverses",
        backup=False,
    )
    db_session.commit()

    cancel_checks = {"count": 0}

    def cancel_after_first_checkpoint() -> bool:
        cancel_checks["count"] += 1
        return cancel_checks["count"] > 1

    result = apply_review_undo_run(
        db_session,
        undo.undo_run_id,
        library_root=library,
        blob_store=store,
        should_cancel=cancel_after_first_checkpoint,
    )

    assert result.state == "undone"
    assert cancel_checks["count"] == 1
    assert source.exists() and not moved.exists()
    assert read_track(source).title != "Changed"
    journals = list(
        db_session.scalars(
            select(ReviewFileJournal).where(
                ReviewFileJournal.apply_run_id == apply_run_id,
                ReviewFileJournal.state == "rolled_back",
            )
        )
    )
    assert {journal.phase for journal in journals} == {"tags", "move"}
    db_session.refresh(track)
    assert track.path == str(source)


def test_undo_reverses_its_own_move_before_restoring_tags(
    tmp_path: Path, db_session: Session
) -> None:
    from muzilla.changes.bundle_undo import apply_review_undo_run
    from muzilla.services.review_undo import enqueue_review_undo

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    moved = library / "song.mp3.renamed"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        move_after_tags=True,
    )
    store = BlobStore(tmp_path / "blobs")
    applied = apply_review_run(db_session, apply_run_id, library_root=library, blob_store=store)
    assert applied.state == "applied"
    assert not source.exists() and moved.exists()
    undo = enqueue_review_undo(
        db_session,
        applied.review_bundle_id,
        apply_run_id=apply_run_id,
        idempotency_key="move-then-tags-undo",
        backup=False,
    )
    db_session.commit()
    result = apply_review_undo_run(
        db_session, undo.undo_run_id, library_root=library, blob_store=store
    )
    assert result.state == "undone"
    assert source.exists() and not moved.exists()
    db_session.refresh(track)
    assert track.path == str(source)
    assert track.title != "Changed"


@pytest.mark.parametrize(
    "filename",
    ["silence.mp3", "silence.flac", "silence.m4a", "silence.ogg", "silence.opus"],
)
@pytest.mark.parametrize("outcome", ["undo", "rollback"])
def test_native_art_collection_and_catalog_identity_survive_inverse(
    tmp_path: Path, db_session: Session, filename: str, outcome: str
) -> None:
    from sqlalchemy import select

    from muzilla.changes.bundle_undo import apply_review_undo_run
    from muzilla.db.models import ReviewFileJournal
    from muzilla.services.review_undo import enqueue_review_undo
    from muzilla.tags.writer import capture_embedded_art

    library = tmp_path / "library"
    library.mkdir()
    source = library / filename
    shutil.copy2(Path("tests/fixtures/audio") / filename, source)
    _add_native_art_collection(source)
    expected_art = capture_embedded_art(source)
    store = BlobStore(tmp_path / "blobs")
    catalog_blob = store.put(db_session, b"different catalog-cover identity", mime="image/jpeg")
    db_session.commit()
    original_inode = source.stat().st_ino
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        remove_art=True,
        catalog_art_blob_id=catalog_blob.id,
        move_after_tags=outcome == "rollback",
    )

    if outcome == "rollback":
        with patch(
            "muzilla.changes.bundle_applier.write_move",
            return_value=(False, "injected move failure"),
        ):
            result = apply_review_run(
                db_session, apply_run_id, library_root=library, blob_store=store
            )
        assert result.state == "failed"
        assert not result.recovery_required
    else:
        result = apply_review_run(db_session, apply_run_id, library_root=library, blob_store=store)
        assert result.state == "applied", result
        undo = enqueue_review_undo(
            db_session,
            result.review_bundle_id,
            apply_run_id=apply_run_id,
            idempotency_key=f"native-art-undo-{filename}",
            backup=False,
        )
        db_session.commit()
        undo_result = apply_review_undo_run(
            db_session,
            undo.undo_run_id,
            library_root=library,
            blob_store=store,
        )
        assert undo_result.state == "undone"

    assert capture_embedded_art(source) == expected_art
    assert source.stat().st_ino != original_inode
    db_session.refresh(track)
    assert track.art_blob_id == catalog_blob.id
    assert track.has_embedded_art
    tag_journal = db_session.scalar(
        select(ReviewFileJournal).where(
            ReviewFileJournal.apply_run_id == apply_run_id,
            ReviewFileJournal.track_id == track.id,
            ReviewFileJournal.phase == "tags",
        )
    )
    assert tag_journal is not None
    inverse_value = tag_journal.before_blob["__muzilla_embedded_art"]
    assert isinstance(inverse_value, dict)
    inverse = inverse_value
    assert inverse["version"] == 1
    entries = inverse["entries"]
    assert isinstance(entries, list)
    assert len(entries) == 2
    assert all(
        isinstance(entry, dict) and isinstance(entry.get("blob_id"), int) for entry in entries
    )


def _recover_apply_in_new_process(
    *, db_path: Path, library: Path, blobs: Path, apply_run_id: int
) -> dict[str, Any]:
    import json
    import subprocess
    import sys

    script = """
import json, sys
from pathlib import Path
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.bundle_applier import recover_apply_runs
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import ApplyRun
engine = create_db_engine(Path(sys.argv[1]))
factory = create_session_factory(engine)
with factory() as session:
    recovered = recover_apply_runs(session, library_root=Path(sys.argv[2]), blob_store=BlobStore(Path(sys.argv[3])))
    run = session.get(ApplyRun, int(sys.argv[4]))
    session.commit()
    print(json.dumps({"recovered": recovered, "result": run.result if run else None}))
engine.dispose()
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(db_path), str(library), str(blobs), str(apply_run_id)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _sqlite_database_path(session: Session) -> Path:
    from sqlalchemy.engine import Engine

    engine = cast(Engine, session.get_bind())
    assert engine.url.database is not None
    return Path(engine.url.database)


def _crash_case_move_process(
    *,
    db_path: Path,
    library: Path,
    blobs: Path,
    run_id: int,
    operation: str,
    checkpoint: str,
) -> int:
    import subprocess
    import sys

    script = """
import errno, os, sys
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from muzilla.changes import writer
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.bundle_applier import apply_review_run
from muzilla.changes.bundle_undo import apply_review_undo_run
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import ReviewFileJournal
engine = create_db_engine(Path(sys.argv[1]))
factory = create_session_factory(engine)
library = Path(sys.argv[2])
blobs = Path(sys.argv[3])
run_id = int(sys.argv[4])
operation = sys.argv[5]
step = sys.argv[6]
mode = sys.argv[7]
alias_names = ("Track.mp3", "track.mp3") if operation == "apply" else ("track.mp3", "Track.mp3")
real_rename = writer._rename_entry_no_replace
refused = [False]
def controlled_rename(source_fd, source_name, destination_fd, destination_name):
    if mode == "fallback" and (source_name, destination_name) == alias_names and not refused[0]:
        refused[0] = True
        raise FileExistsError(errno.EEXIST, "injected native case-rename refusal")
    result = real_rename(source_fd, source_name, destination_fd, destination_name)
    if mode == "direct" and (source_name, destination_name) == alias_names:
        os._exit(86)
    return result
writer._rename_entry_no_replace = controlled_rename
real_commit = Session.commit
def crash_at_checkpoint(self, *args, **kwargs):
    result = real_commit(self, *args, **kwargs)
    if mode == "fallback":
        phase_key = "__muzilla_case_move" if operation == "apply" else "__muzilla_case_inverse"
        journal = self.scalar(select(ReviewFileJournal).where(ReviewFileJournal.phase == "move"))
        checkpoint = (journal.before_blob or {}).get(phase_key) if journal is not None else None
        if journal is not None and journal.state == "writing" and isinstance(checkpoint, dict) and checkpoint.get("step") == step:
            os._exit(87)
    return result
Session.commit = crash_at_checkpoint
with factory() as session:
    if operation == "apply":
        apply_review_run(session, run_id, library_root=library, blob_store=BlobStore(blobs))
    else:
        apply_review_undo_run(session, run_id, library_root=library, blob_store=BlobStore(blobs))
engine.dispose()
os._exit(2)
"""
    mode = "direct" if checkpoint == "direct" else "fallback"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(db_path),
            str(library),
            str(blobs),
            str(run_id),
            operation,
            checkpoint,
            mode,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return completed.returncode


def _crash_publication_process(
    *,
    db_path: Path,
    library: Path,
    blobs: Path,
    run_id: int,
    operation: str,
    fault: str,
) -> int:
    import subprocess
    import sys

    script = """
import os, sys
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from muzilla.changes import writer
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.bundle_applier import apply_review_run
from muzilla.changes.bundle_undo import apply_review_undo_run
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import ReviewFileJournal
engine = create_db_engine(Path(sys.argv[1]))
factory = create_session_factory(engine)
library = Path(sys.argv[2])
blobs = Path(sys.argv[3])
run_id = int(sys.argv[4])
operation = sys.argv[5]
fault = sys.argv[6]
target_purpose = "apply" if operation == "apply" else "restore"
active = {"purpose": None}
real_rename = writer._rename_entry_no_replace

def controlled_rename(source_fd, source_name, destination_fd, destination_name):
    result = real_rename(source_fd, source_name, destination_fd, destination_name)
    if operation != "undo" and active["purpose"] == target_purpose:
        if fault == "after-displacement-syscall" and source_name == "song.mp3" and destination_name == "original":
            os._exit(92)
        if fault == "after-publication-syscall" and source_name == "staged" and destination_name == "song.mp3":
            os._exit(92)
    if operation == "undo" and active["purpose"] == target_purpose:
        if fault == "after-displacement-syscall" and source_name == "song.mp3" and destination_name == "original":
            os._exit(92)
        if fault == "after-publication-syscall" and source_name == "staged" and destination_name == "song.mp3":
            os._exit(92)
    return result
writer._rename_entry_no_replace = controlled_rename
if operation == "rollback":
    from muzilla.changes import bundle_applier
    bundle_applier.write_move = lambda *args, **kwargs: (False, "injected move failure")
real_commit = Session.commit
def crash_at_transition_checkpoint(self, *args, **kwargs):
    result = real_commit(self, *args, **kwargs)
    journals = self.scalars(select(ReviewFileJournal).where(ReviewFileJournal.phase == "tags"))
    for journal in journals:
        transition = (journal.before_blob or {}).get("__muzilla_publication_transition")
        if isinstance(transition, dict):
            active["purpose"] = transition.get("purpose")
            if (
                transition.get("purpose") == target_purpose
                and transition.get("phase") == fault
            ):
                os._exit(91)
    return result
Session.commit = crash_at_transition_checkpoint
with factory() as session:
    if operation in {"apply", "rollback"}:
        apply_review_run(session, run_id, library_root=library, blob_store=BlobStore(blobs))
    else:
        apply_review_undo_run(session, run_id, library_root=library, blob_store=BlobStore(blobs))
engine.dispose()
os._exit(2)
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(db_path),
            str(library),
            str(blobs),
            str(run_id),
            operation,
            fault,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return completed.returncode


def _recover_undo_job_in_new_process(
    *, db_path: Path, library: Path, job_id: int, undo_run_id: int
) -> dict[str, Any]:
    import json
    import subprocess
    import sys

    script = """
import json, sys
from pathlib import Path
from sqlalchemy import select
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Job, ReviewFileJournal, ReviewUndoRun
from muzilla.jobs.queue import recover_stuck_jobs
engine = create_db_engine(Path(sys.argv[1]))
factory = create_session_factory(engine)
with factory() as session:
    recovered = recover_stuck_jobs(session, library_root=Path(sys.argv[2]))
    job = session.get(Job, int(sys.argv[3]))
    undo = session.get(ReviewUndoRun, int(sys.argv[4]))
    journal = session.scalar(select(ReviewFileJournal).where(ReviewFileJournal.phase == "move"))
    print(json.dumps({
        "recovered": recovered,
        "job_state": job.state if job else None,
        "undo_state": undo.state if undo else None,
        "undo_result": undo.result if undo else None,
        "journal_state": journal.state if journal else None,
    }))
engine.dispose()
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(db_path), str(library), str(job_id), str(undo_run_id)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _expire_undo_job(session: Session, job_id: int) -> None:
    from datetime import timedelta

    from muzilla.db.models import Job

    job = session.get(Job, job_id)
    assert job is not None
    job.state = "running"
    job.worker_id = "crashed-case-rename-test"
    job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    job.attempts = max(job.attempts, 1)
    session.commit()


@pytest.mark.parametrize("operation", ["apply", "rollback", "undo"])
@pytest.mark.parametrize(
    "fault",
    [
        "displace_intent",
        "displaced",
        "publication_intent",
        "published",
        "after-displacement-syscall",
        "after-publication-syscall",
    ],
)
def test_tag_replacement_crash_checkpoints_recover_after_restart(
    tmp_path: Path, db_session: Session, operation: str, fault: str
) -> None:
    from sqlalchemy import select

    from muzilla.db.models import ApplyRun, ReviewFileJournal
    from muzilla.services.review_undo import enqueue_review_undo

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    original = source.read_bytes()
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        move_after_tags=operation == "rollback",
    )
    store = BlobStore(tmp_path / "blobs")
    run_id = apply_run_id
    job_id: int | None = None
    if operation == "undo":
        applied = apply_review_run(db_session, apply_run_id, library_root=library, blob_store=store)
        assert applied.state == "applied", applied
        applied_bytes = source.read_bytes()
        undo = enqueue_review_undo(
            db_session,
            applied.review_bundle_id,
            apply_run_id=apply_run_id,
            idempotency_key=f"publication-crash-{fault}",
            backup=False,
        )
        db_session.commit()
        run_id = undo.undo_run_id
        job_id = undo.job_id
    else:
        applied_bytes = b""

    expected_exit = 92 if fault.startswith("after-") else 91
    assert (
        _crash_publication_process(
            db_path=_sqlite_database_path(db_session),
            library=library,
            blobs=tmp_path / "blobs",
            run_id=run_id,
            operation=operation,
            fault=fault,
        )
        == expected_exit
    )

    if operation == "undo":
        assert job_id is not None
        _expire_undo_job(db_session, job_id)
        recovered = _recover_undo_job_in_new_process(
            db_path=_sqlite_database_path(db_session),
            library=library,
            job_id=job_id,
            undo_run_id=run_id,
        )
        assert recovered["recovered"] == 1
        assert recovered["undo_state"] == "failed"
        assert recovered["undo_result"]["recovery_required"] is True
        assert source.read_bytes() == applied_bytes
        db_session.expire_all()
        tag_journal = db_session.scalar(
            select(ReviewFileJournal).where(
                ReviewFileJournal.apply_run_id == apply_run_id,
                ReviewFileJournal.phase == "tags",
            )
        )
        assert tag_journal is not None and tag_journal.state == "done"
        assert read_track(source).title == "Changed"
    else:
        recovered_apply = _recover_apply_in_new_process(
            db_path=_sqlite_database_path(db_session),
            library=library,
            blobs=tmp_path / "blobs",
            apply_run_id=apply_run_id,
        )
        assert recovered_apply["recovered"] == 1
        assert recovered_apply["result"]["state"] == "failed"
        assert recovered_apply["result"]["recovery_required"] is False
        if operation == "apply":
            assert source.read_bytes() == original
        else:
            assert read_track(source).title != "Changed"
        db_session.expire_all()
        tag_journal = db_session.scalar(
            select(ReviewFileJournal).where(
                ReviewFileJournal.apply_run_id == apply_run_id,
                ReviewFileJournal.phase == "tags",
            )
        )
        assert tag_journal is not None
        assert tag_journal.state == ("failed" if operation == "apply" else "rolled_back")
        run = db_session.get(ApplyRun, apply_run_id)
        assert run is not None and run.result is not None
        assert run.result["recovery_required"] is False
        db_session.refresh(track)
        assert track.title != "Changed"

    assert list((library / ".muzilla-private").iterdir()) == []


@pytest.mark.parametrize("operation", ["apply", "undo"])
def test_case_only_atomic_rename_crash_recovers_after_restart_on_real_filesystem(
    tmp_path: Path, db_session: Session, operation: str
) -> None:
    from muzilla.services.review_undo import enqueue_review_undo

    library = tmp_path / "library"
    library.mkdir()
    source = library / "Track.mp3"
    destination = library / "track.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        move_after_tags=True,
        move_destination=destination.name,
    )
    store = BlobStore(tmp_path / "blobs")
    run_id = apply_run_id
    job_id: int | None = None
    if operation == "undo":
        applied = apply_review_run(db_session, apply_run_id, library_root=library, blob_store=store)
        assert applied.state == "applied", applied
        undo = enqueue_review_undo(
            db_session,
            applied.review_bundle_id,
            apply_run_id=apply_run_id,
            idempotency_key="case-only-direct-crash-undo",
            backup=False,
        )
        db_session.commit()
        run_id = undo.undo_run_id
        job_id = undo.job_id

    assert (
        _crash_case_move_process(
            db_path=_sqlite_database_path(db_session),
            library=library,
            blobs=tmp_path / "blobs",
            run_id=run_id,
            operation=operation,
            checkpoint="direct",
        )
        == 86
    )

    if operation == "apply":
        recovered = _recover_apply_in_new_process(
            db_path=_sqlite_database_path(db_session),
            library=library,
            blobs=tmp_path / "blobs",
            apply_run_id=apply_run_id,
        )
        assert recovered["recovered"] == 1
        assert recovered["result"]["recovery_required"] is True
        assert _library_entry_names(library) == [source.name]
        assert read_track(source).title != "Changed"
    else:
        assert job_id is not None
        _expire_undo_job(db_session, job_id)
        recovered = _recover_undo_job_in_new_process(
            db_path=_sqlite_database_path(db_session),
            library=library,
            job_id=job_id,
            undo_run_id=run_id,
        )
        assert recovered["recovered"] == 1
        assert recovered["job_state"] == "failed"
        assert recovered["undo_state"] == "failed"
        assert recovered["undo_result"]["recovery_required"] is True
        assert recovered["journal_state"] == "done"
        assert _library_entry_names(library) == [destination.name]
        assert read_track(destination).title == "Changed"
        db_session.expire_all()
        db_session.refresh(track)
        assert track.path == str(destination)


@pytest.mark.parametrize("operation", ["apply", "undo"])
@pytest.mark.parametrize("checkpoint", ["first_intent", "first_done", "final_intent", "final_done"])
def test_case_only_intermediate_checkpoints_recover_after_restart(
    tmp_path: Path, db_session: Session, operation: str, checkpoint: str
) -> None:
    from sqlalchemy import select

    from muzilla.db.models import ReviewFileJournal
    from muzilla.services.review_undo import enqueue_review_undo

    library = tmp_path / "library"
    library.mkdir()
    source = library / "Track.mp3"
    destination = library / "track.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    if not destination.exists() or not destination.samefile(source):
        pytest.skip("journaled fallback checkpoints require a case-insensitive filesystem")
    track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        move_after_tags=True,
        move_destination=destination.name,
    )
    store = BlobStore(tmp_path / "blobs")
    run_id = apply_run_id
    job_id: int | None = None
    if operation == "undo":
        applied = apply_review_run(db_session, apply_run_id, library_root=library, blob_store=store)
        assert applied.state == "applied", applied
        undo = enqueue_review_undo(
            db_session,
            applied.review_bundle_id,
            apply_run_id=apply_run_id,
            idempotency_key=f"case-only-fallback-{checkpoint}",
            backup=False,
        )
        db_session.commit()
        run_id = undo.undo_run_id
        job_id = undo.job_id

    assert (
        _crash_case_move_process(
            db_path=_sqlite_database_path(db_session),
            library=library,
            blobs=tmp_path / "blobs",
            run_id=run_id,
            operation=operation,
            checkpoint=checkpoint,
        )
        == 87
    )

    if operation == "apply":
        recovered = _recover_apply_in_new_process(
            db_path=_sqlite_database_path(db_session),
            library=library,
            blobs=tmp_path / "blobs",
            apply_run_id=apply_run_id,
        )
        assert recovered["recovered"] == 1
        assert recovered["result"]["recovery_required"] is True
        assert _library_entry_names(library) == [source.name]
        assert read_track(source).title != "Changed"
        db_session.expire_all()
        move_journal = db_session.scalar(
            select(ReviewFileJournal).where(
                ReviewFileJournal.apply_run_id == apply_run_id,
                ReviewFileJournal.phase == "move",
            )
        )
        assert move_journal is not None and move_journal.state == "rolled_back"
    else:
        assert job_id is not None
        _expire_undo_job(db_session, job_id)
        recovered = _recover_undo_job_in_new_process(
            db_path=_sqlite_database_path(db_session),
            library=library,
            job_id=job_id,
            undo_run_id=run_id,
        )
        assert recovered["recovered"] == 1
        assert recovered["job_state"] == "failed"
        assert recovered["undo_state"] == "failed"
        assert recovered["undo_result"]["recovery_required"] is True
        assert recovered["journal_state"] == "done"
        assert _library_entry_names(library) == [destination.name]
        assert read_track(destination).title == "Changed"
        db_session.expire_all()
        db_session.refresh(track)
        assert track.path == str(destination)


def test_post_replace_directory_fsync_failure_is_recovery_required_after_restart(
    tmp_path: Path, db_session: Session
) -> None:
    import os
    import stat

    from sqlalchemy import select

    from muzilla.db.models import ApplyRun, ReviewFileJournal

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    _track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path, db_session=db_session, path=source, new_title="Changed"
    )
    real_fsync = os.fsync
    library_info = library.stat()
    parent_fsyncs = 0

    def fail_directory_fsync(file_fd: int) -> None:
        nonlocal parent_fsyncs
        actual = os.fstat(file_fd)
        if stat.S_ISDIR(actual.st_mode) and (actual.st_dev, actual.st_ino) == (
            library_info.st_dev,
            library_info.st_ino,
        ):
            parent_fsyncs += 1
            if parent_fsyncs == 3:
                raise OSError("directory fsync fault after replacement")
        real_fsync(file_fd)

    with patch("muzilla.changes.writer.os.fsync", side_effect=fail_directory_fsync):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=BlobStore(tmp_path / "blobs"),
        )

    assert result.state == "failed"
    assert result.recovery_required
    assert read_track(source).title == "Changed"
    run = db_session.get(ApplyRun, apply_run_id)
    assert run is not None and run.result is not None
    assert run.result["recovery_required"] is True
    from sqlalchemy.engine import Engine

    db_engine = cast(Engine, db_session.get_bind())
    database_path = db_engine.url.database
    assert database_path is not None
    restarted = _recover_apply_in_new_process(
        db_path=Path(database_path),
        library=library,
        blobs=tmp_path / "blobs",
        apply_run_id=apply_run_id,
    )
    assert restarted["recovered"] == 1
    assert restarted["result"]["recovery_required"] is False
    assert restarted["result"]["state"] == "failed"
    assert read_track(source).title != "Changed"
    journal = db_session.scalar(
        select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == apply_run_id)
    )
    assert journal is not None and journal.state == "failed"


@pytest.mark.parametrize("fault", ["reread", "checkpoint"])
def test_post_replace_database_or_reread_failure_recovers_after_restart(
    tmp_path: Path, db_session: Session, fault: str
) -> None:
    from sqlalchemy import select, text

    from muzilla.db.models import ApplyRun, ReviewFileJournal
    from muzilla.tags.reader import TagReadError

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    _track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path, db_session=db_session, path=source, new_title="Changed"
    )

    if fault == "reread":
        with patch(
            "muzilla.changes.writer.read_track",
            side_effect=TagReadError(source, ValueError("injected reread failure")),
        ):
            result = apply_review_run(
                db_session,
                apply_run_id,
                library_root=library,
                blob_store=BlobStore(tmp_path / "blobs"),
            )
    else:
        real_flush = db_session.flush

        def fail_done_checkpoint(*args: Any, **kwargs: Any) -> None:
            if any(
                isinstance(item, ReviewFileJournal) and item.state == "done"
                for item in db_session.dirty
            ):
                with db_session.no_autoflush:
                    db_session.execute(text("INSERT INTO injected_missing_table VALUES (1)"))
            real_flush(*args, **kwargs)

        with patch.object(db_session, "flush", side_effect=fail_done_checkpoint):
            result = apply_review_run(
                db_session,
                apply_run_id,
                library_root=library,
                blob_store=BlobStore(tmp_path / "blobs"),
            )

    assert result.state == "failed"
    assert result.recovery_required
    assert read_track(source).title == "Changed"
    journal = db_session.scalar(
        select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == apply_run_id)
    )
    assert journal is not None and journal.state == "writing"
    run = db_session.get(ApplyRun, apply_run_id)
    assert run is not None and run.result is not None
    assert run.result["recovery_required"] is True
    from sqlalchemy.engine import Engine

    db_engine = cast(Engine, db_session.get_bind())
    database_path = db_engine.url.database
    assert database_path is not None
    restarted = _recover_apply_in_new_process(
        db_path=Path(database_path),
        library=library,
        blobs=tmp_path / "blobs",
        apply_run_id=apply_run_id,
    )
    assert restarted["recovered"] == 1
    assert restarted["result"]["recovery_required"] is False
    assert read_track(source).title != "Changed"
    db_session.expire_all()
    journal = db_session.scalar(
        select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == apply_run_id)
    )
    assert journal is not None and journal.state == "failed"


def test_rollback_database_failure_invalidates_session_before_persisting_journal(
    tmp_path: Path, db_session: Session
) -> None:
    from sqlalchemy import select, text

    from muzilla.db.models import ApplyRun, ReviewFileJournal

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    _track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        move_after_tags=True,
    )
    real_flush = db_session.flush

    def fail_rollback_checkpoint(*args: Any, **kwargs: Any) -> None:
        if any(
            isinstance(item, ReviewFileJournal) and item.state == "rolled_back"
            for item in db_session.dirty
        ):
            with db_session.no_autoflush:
                db_session.execute(text("INSERT INTO injected_missing_table VALUES (1)"))
        real_flush(*args, **kwargs)

    with (
        patch("muzilla.changes.bundle_applier.write_move", return_value=(False, "move fault")),
        patch.object(db_session, "flush", side_effect=fail_rollback_checkpoint),
    ):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=BlobStore(tmp_path / "blobs"),
        )

    assert result.state == "failed"
    assert result.recovery_required
    assert read_track(source).title != "Changed"
    assert db_session.is_active
    journal = db_session.scalar(
        select(ReviewFileJournal).where(
            ReviewFileJournal.apply_run_id == apply_run_id,
            ReviewFileJournal.phase == "tags",
        )
    )
    assert journal is not None and journal.state == "writing"
    assert "rollback failed" in (journal.error or "")
    run = db_session.get(ApplyRun, apply_run_id)
    assert run is not None and run.result is not None
    assert run.result["recovery_required"] is True


def test_post_move_directory_fsync_failure_is_recovery_required_after_restart(
    tmp_path: Path, db_session: Session
) -> None:
    from sqlalchemy import select

    from muzilla.db.models import ApplyRun, ReviewFileJournal

    library = tmp_path / "library"
    library.mkdir()
    source = library / "song.mp3"
    destination = library / "song.mp3.renamed"
    shutil.copy2(Path("tests/fixtures/audio/silence.mp3"), source)
    _track, apply_run_id = _enqueue_title_change(
        tmp_path=tmp_path,
        db_session=db_session,
        path=source,
        new_title="Changed",
        move_after_tags=True,
    )

    with patch(
        "muzilla.changes.writer._fsync_directory",
        side_effect=OSError("injected move directory fsync failure"),
    ):
        result = apply_review_run(
            db_session,
            apply_run_id,
            library_root=library,
            blob_store=BlobStore(tmp_path / "blobs"),
        )

    assert result.state == "failed"
    assert result.recovery_required
    assert not source.exists() and destination.exists()
    move_journal = db_session.scalar(
        select(ReviewFileJournal).where(
            ReviewFileJournal.apply_run_id == apply_run_id,
            ReviewFileJournal.phase == "move",
        )
    )
    assert move_journal is not None and move_journal.state == "writing"
    run = db_session.get(ApplyRun, apply_run_id)
    assert run is not None and run.result is not None
    assert run.result["recovery_required"] is True
    from sqlalchemy.engine import Engine

    db_engine = cast(Engine, db_session.get_bind())
    database_path = db_engine.url.database
    assert database_path is not None
    restarted = _recover_apply_in_new_process(
        db_path=Path(database_path),
        library=library,
        blobs=tmp_path / "blobs",
        apply_run_id=apply_run_id,
    )
    assert restarted["recovered"] == 1
    assert restarted["result"]["recovery_required"] is True
