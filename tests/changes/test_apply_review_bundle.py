from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.applier import recover_apply_journal
from muzilla.changes.backup import BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.bundle_applier import (
    apply_review_run,
    build_undo_changesets_for_run,
)
from muzilla.db.models import ApplyJournal, ApplyRun, Operation, Track
from muzilla.domain.reviews import BundleState
from muzilla.pipeline.reviews import (
    OperationDraft,
    put_revision,
    start_apply_run,
    transition_bundle,
)
from muzilla.pipeline.scan import scan_library
from muzilla.tags.hashing import partial_content_hash
from muzilla.tags.reader import read_lyrics, read_track
from muzilla.tags.writer import write_fields, write_lyrics

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _scan_tracks(session: Session, tmp_path: Path, names: tuple[str, ...]) -> tuple[Path, list[Track]]:
    library = tmp_path / "library"
    library.mkdir()
    for name in names:
        shutil.copy(FIXTURES / name, library / name)
    scan_library(session, library)
    session.commit()
    tracks = list(session.scalars(select(Track).order_by(Track.filename)))
    return library, tracks


def _snapshot(*tracks: Track) -> dict[str, object]:
    return {
        "items": [
            {
                "source_type": "track",
                "source_id": track.id,
                "path": track.path,
                "filename": track.filename,
                "size_bytes": track.size_bytes,
                "mtime_ns": track.mtime_ns,
                "content_hash": track.content_hash,
                "tag_hash": track.tag_hash,
            }
            for track in tracks
        ]
    }


def _ready_run(
    session: Session,
    tracks: list[Track],
    operations: tuple[OperationDraft, ...],
    *,
    key: str = "apply-review",
) -> ApplyRun:
    write = put_revision(
        session,
        logical_key="group:test" if len(tracks) > 1 else f"track:{tracks[0].id}",
        title="Bundle apply test",
        scope_type="group" if len(tracks) > 1 else "track",
        scope_id=None if len(tracks) > 1 else tracks[0].id,
        source_snapshot=_snapshot(*tracks),
        operations=operations,
    )
    for operation in session.scalars(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    ):
        operation.decision = "accepted"
    transition_bundle(session, write.bundle_id, BundleState.READY)
    run = start_apply_run(session, write.bundle_id, idempotency_key=key)
    session.commit()
    return run


def test_bundle_apply_writes_all_tag_sections_once_then_moves(
    db_session: Session, tmp_path: Path
) -> None:
    library, tracks = _scan_tracks(db_session, tmp_path, ("silence.mp3",))
    track = tracks[0]
    original_bytes = Path(track.path).read_bytes()
    destination = library / "Artist - Reviewed.mp3"
    store = BlobStore(tmp_path / "blobs")
    backup_store = BackupStore(tmp_path / "backups", library_root=library)
    blob = store.put(db_session, b"\xff\xd8\xff\xe0" + b"review cover" * 20, mime="image/jpeg")
    db_session.commit()
    run = _ready_run(
        db_session,
        tracks,
        (
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value=track.title,
                proposed_value="Reviewed",
            ),
            OperationDraft(
                kind="write_lyrics",
                field="lyrics",
                target_type="track",
                target_id=track.id,
                current_value=None,
                proposed_value={"text": "bundle lyrics", "synced": False, "provider": "test"},
            ),
            OperationDraft(
                kind="embed_art",
                field="art",
                target_type="track",
                target_id=track.id,
                current_value=None,
                proposed_value={"blob_id": blob.id},
            ),
            OperationDraft(
                kind="set_replay_gain",
                field="rg_track_gain",
                target_type="track",
                target_id=track.id,
                current_value=None,
                proposed_value=-7.25,
            ),
            OperationDraft(
                kind="move_file",
                field="path",
                target_type="track",
                target_id=track.id,
                current_value=track.path,
                proposed_value=str(destination),
            ),
        ),
    )

    result = apply_review_run(
        db_session,
        run.id,
        library_root=library,
        create_directories=False,
        blob_store=store,
        backup_store=backup_store,
    )
    db_session.commit()

    assert result.state == "applied"
    assert [(item.track_id, item.state) for item in result.files] == [(track.id, "applied")]
    assert destination.exists()
    assert not (library / "silence.mp3").exists()
    meta = read_track(destination)
    assert meta.title == "Reviewed"
    assert meta.rg_track_gain == pytest.approx(-7.25)
    assert meta.has_embedded_art is True
    assert read_lyrics(destination) == "bundle lyrics"
    assert (tmp_path / "backups" / "silence.mp3").read_bytes() == original_bytes
    journals = list(
        db_session.scalars(select(ApplyJournal).order_by(ApplyJournal.id))
    )
    assert [(journal.phase, journal.state) for journal in journals] == [
        ("tags", "done"),
        ("move", "done"),
    ]
    assert not list(library.rglob("*.muzilla.tmp"))


def test_source_snapshot_precondition_skips_file_changed_after_review(
    db_session: Session, tmp_path: Path
) -> None:
    library, tracks = _scan_tracks(db_session, tmp_path, ("silence.mp3",))
    track = tracks[0]
    run = _ready_run(
        db_session,
        tracks,
        (
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value=track.title,
                proposed_value="Reviewed",
            ),
        ),
    )
    write_fields(Path(track.path), {"title": "External edit"})

    result = apply_review_run(db_session, run.id, library_root=library)
    db_session.commit()

    assert result.state == "failed"
    assert result.files[0].state == "skipped"
    assert "snapshot" in (result.files[0].error or "")
    assert read_track(Path(track.path)).title == "External edit"


def test_retry_without_successful_side_effect_keeps_frozen_snapshot(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library, tracks = _scan_tracks(db_session, tmp_path, ("silence.mp3",))
    track = tracks[0]
    source = Path(track.path)
    run = _ready_run(
        db_session,
        tracks,
        (
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value=track.title,
                proposed_value="Reviewed",
            ),
        ),
    )
    original_replace = os.replace

    def fail_before_replace(source_path: str | Path, destination: str | Path) -> None:
        if Path(destination) == source:
            raise OSError("injected failure before replace")
        original_replace(source_path, destination)

    monkeypatch.setattr("muzilla.changes.applier.os.replace", fail_before_replace)
    first = apply_review_run(db_session, run.id, library_root=library)
    assert first.state == "failed"
    assert read_track(source).title != "Reviewed"

    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    monkeypatch.setattr("muzilla.changes.applier.os.replace", original_replace)

    retry = apply_review_run(db_session, run.id, library_root=library)

    assert retry.state == "failed"
    assert retry.files[0].state == "skipped"
    assert "snapshot" in (retry.files[0].error or "")
    assert read_track(source).title != "Reviewed"


def test_partial_bundle_retry_does_not_repeat_successful_file_side_effects(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library, tracks = _scan_tracks(db_session, tmp_path, ("silence.flac", "silence.mp3"))
    first, second = tracks
    run = _ready_run(
        db_session,
        tracks,
        tuple(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value=track.title,
                proposed_value=f"Reviewed {track.filename}",
            )
            for track in tracks
        ),
    )
    original_replace = os.replace
    failed_once = False

    def fail_second_tmp(source: str | Path, destination: str | Path) -> None:
        nonlocal failed_once
        if not failed_once and Path(destination) == Path(second.path):
            failed_once = True
            raise OSError("injected replace failure")
        original_replace(source, destination)

    monkeypatch.setattr("muzilla.changes.applier.os.replace", fail_second_tmp)
    first_result = apply_review_run(db_session, run.id, library_root=library)
    db_session.commit()
    first_stat = Path(first.path).stat()

    assert first_result.state == "partially_applied"
    assert {item.track_id: item.state for item in first_result.files} == {
        first.id: "applied",
        second.id: "failed",
    }

    monkeypatch.setattr("muzilla.changes.applier.os.replace", original_replace)
    second_result = apply_review_run(db_session, run.id, library_root=library)
    db_session.commit()

    assert second_result.state == "applied"
    assert {item.state for item in second_result.files} == {"applied"}
    assert Path(first.path).stat().st_mtime_ns == first_stat.st_mtime_ns
    assert read_track(Path(first.path)).title == f"Reviewed {first.filename}"
    assert read_track(Path(second.path)).title == f"Reviewed {second.filename}"


def test_bundle_move_refuses_late_collision(
    db_session: Session, tmp_path: Path
) -> None:
    library, tracks = _scan_tracks(db_session, tmp_path, ("silence.mp3",))
    track = tracks[0]
    collision = library / "occupied.mp3"
    shutil.copy(FIXTURES / "silence.flac", collision)
    collision_bytes = collision.read_bytes()
    run = _ready_run(
        db_session,
        tracks,
        (
            OperationDraft(
                kind="move_file",
                field="path",
                target_type="track",
                target_id=track.id,
                current_value=track.path,
                proposed_value=str(collision),
            ),
        ),
    )

    result = apply_review_run(db_session, run.id, library_root=library)
    db_session.commit()

    assert result.files[0].state == "failed"
    assert collision.read_bytes() == collision_bytes
    assert Path(track.path).exists()


def test_bundle_move_never_clobbers_collision_created_after_precheck(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library, tracks = _scan_tracks(db_session, tmp_path, ("silence.mp3",))
    track = tracks[0]
    source = Path(track.path)
    destination = library / "late-collision.mp3"
    competitor_bytes = b"late collision must survive"
    run = _ready_run(
        db_session,
        tracks,
        (
            OperationDraft(
                kind="move_file",
                field="path",
                target_type="track",
                target_id=track.id,
                current_value=track.path,
                proposed_value=str(destination),
            ),
        ),
    )
    original_exists = Path.exists
    injected = False

    def inject_after_precheck(path: Path) -> bool:
        nonlocal injected
        if path == destination and not injected:
            destination.write_bytes(competitor_bytes)
            injected = True
            return False
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", inject_after_precheck)

    result = apply_review_run(db_session, run.id, library_root=library)

    assert result.state == "failed"
    assert result.files[0].state == "failed"
    assert destination.read_bytes() == competitor_bytes
    assert source.exists()


def test_bundle_move_allows_case_only_rename(
    db_session: Session, tmp_path: Path
) -> None:
    library, tracks = _scan_tracks(db_session, tmp_path, ("silence.mp3",))
    track = tracks[0]
    destination = library / "SILENCE.mp3"
    run = _ready_run(
        db_session,
        tracks,
        (
            OperationDraft(
                kind="move_file",
                field="path",
                target_type="track",
                target_id=track.id,
                current_value=track.path,
                proposed_value=str(destination),
            ),
        ),
    )

    result = apply_review_run(db_session, run.id, library_root=library)
    db_session.commit()

    assert result.state == "applied"
    assert destination.exists()
    assert db_session.get(Track, track.id).path == str(destination)  # type: ignore[union-attr]


def test_apply_move_catalog_rescan_does_not_mark_track_missing(
    db_session: Session, tmp_path: Path
) -> None:
    library, tracks = _scan_tracks(db_session, tmp_path, ("silence.mp3",))
    track = tracks[0]
    destination = library / "renamed.mp3"
    run = _ready_run(
        db_session,
        tracks,
        (
            OperationDraft(
                kind="move_file",
                field="path",
                target_type="track",
                target_id=track.id,
                current_value=track.path,
                proposed_value=str(destination),
            ),
        ),
    )

    apply_review_run(db_session, run.id, library_root=library)
    db_session.commit()
    stats = scan_library(db_session, library)
    db_session.expire_all()

    refreshed = db_session.get(Track, track.id)
    assert refreshed is not None
    assert refreshed.path == str(destination)
    assert refreshed.missing_since is None
    assert stats.missing == 0


def test_tag_apply_realigns_catalog_stat_and_hashes_before_rescan(
    db_session: Session, tmp_path: Path
) -> None:
    library, tracks = _scan_tracks(db_session, tmp_path, ("silence.mp3",))
    track = tracks[0]
    run = _ready_run(
        db_session,
        tracks,
        (
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value=track.title,
                proposed_value="Catalog aligned",
            ),
        ),
    )

    apply_review_run(db_session, run.id, library_root=library)
    db_session.expire_all()

    refreshed = db_session.get(Track, track.id)
    assert refreshed is not None
    path = Path(refreshed.path)
    stat = path.stat()
    assert refreshed.size_bytes == stat.st_size
    assert refreshed.mtime_ns == stat.st_mtime_ns
    assert refreshed.content_hash == partial_content_hash(path, stat.st_size)

    stats = scan_library(db_session, library)
    assert stats.unchanged == 1
    assert stats.updated == 0


def test_bundle_apply_undo_restores_preexisting_lyrics_tag_and_path(
    db_session: Session, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    original_path = library / "silence.mp3"
    shutil.copy(FIXTURES / "silence.mp3", original_path)
    write_lyrics(original_path, "pre-existing lyrics")
    scan_library(db_session, library)
    db_session.commit()
    track = db_session.scalar(select(Track))
    assert track is not None
    tracks = [track]
    original_title = track.title
    destination = library / "renamed.mp3"
    store = BlobStore(tmp_path / "blobs")
    blob = store.put(db_session, b"\xff\xd8\xff\xe0" + b"undo cover" * 20, mime="image/jpeg")
    db_session.commit()
    run = _ready_run(
        db_session,
        tracks,
        (
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value=track.title,
                proposed_value="Reviewed",
            ),
            OperationDraft(
                kind="write_lyrics",
                field="lyrics",
                target_type="track",
                target_id=track.id,
                current_value=None,
                proposed_value={"text": "undo lyrics", "synced": False, "provider": "test"},
            ),
            OperationDraft(
                kind="embed_art",
                field="art",
                target_type="track",
                target_id=track.id,
                current_value=None,
                proposed_value={"blob_id": blob.id},
            ),
            OperationDraft(
                kind="set_replay_gain",
                field="rg_track_gain",
                target_type="track",
                target_id=track.id,
                current_value=None,
                proposed_value=-8.5,
            ),
            OperationDraft(
                kind="move_file",
                field="path",
                target_type="track",
                target_id=track.id,
                current_value=track.path,
                proposed_value=str(destination),
            ),
        ),
    )
    apply_review_run(db_session, run.id, library_root=library, blob_store=store)
    db_session.commit()

    undo_changesets = build_undo_changesets_for_run(db_session, run.id)
    db_session.commit()
    from muzilla.changes.applier import apply_changeset

    for changeset in undo_changesets:
        apply_changeset(
            db_session,
            changeset.id,
            library_root=library,
            blob_store=store,
        )
    db_session.commit()

    assert original_path.exists()
    assert not destination.exists()
    restored = read_track(original_path)
    assert restored.title == original_title
    assert restored.rg_track_gain is None
    assert restored.has_embedded_art is False
    assert read_lyrics(original_path) == "pre-existing lyrics"


def test_crash_after_replace_is_recovered_then_retry_is_deterministic(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library, tracks = _scan_tracks(db_session, tmp_path, ("silence.mp3",))
    track = tracks[0]
    original_title = track.title
    run = _ready_run(
        db_session,
        tracks,
        (
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value=track.title,
                proposed_value="Reviewed",
            ),
        ),
    )
    original_replace = os.replace

    def crash_after_replace(source: str | Path, destination: str | Path) -> None:
        original_replace(source, destination)
        if Path(destination) == Path(track.path):
            raise KeyboardInterrupt("injected crash")

    monkeypatch.setattr("muzilla.changes.applier.os.replace", crash_after_replace)
    with pytest.raises(KeyboardInterrupt, match="injected crash"):
        apply_review_run(db_session, run.id, library_root=library)

    monkeypatch.setattr("muzilla.changes.applier.os.replace", original_replace)
    report = recover_apply_journal(db_session)
    assert report.reverted == 1
    assert read_track(Path(track.path)).title == original_title

    result = apply_review_run(db_session, run.id, library_root=library)
    db_session.commit()
    assert result.state == "applied"
    assert read_track(Path(track.path)).title == "Reviewed"
