from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.changes.applier import apply_changeset
from muzilla.changes.backup import BackupStore
from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.db.models import Track
from muzilla.pipeline.scan import scan_library
from muzilla.tags.hashing import partial_content_hash
from muzilla.tags.reader import read_track

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _scan_one(db_session: Session, tmp_path: Path, name: str = "silence.mp3") -> Track:
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / name, library / name)
    scan_library(db_session, library)
    db_session.commit()
    return db_session.query(Track).filter(Track.filename == name).one()


def _stage_title_edit(db_session: Session, track: Track, new_title: str) -> int:
    cs = build_changeset(
        db_session,
        title="Edit",
        source="manual_edit",
        edits={track.id: [FieldEdit(field="title", new_value=new_title, is_manual=True)]},
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()
    return cs.id


def test_apply_with_backup_store_copies_original_before_write(
    db_session: Session, tmp_path: Path
) -> None:
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    original_bytes = (library / "silence.mp3").read_bytes()
    backup_store = BackupStore(tmp_path / "backups", library_root=library)

    cs_id = _stage_title_edit(db_session, track, "New Title")
    result = apply_changeset(db_session, cs_id, backup_store=backup_store)
    db_session.commit()

    assert result.state == "applied"
    backup_path = tmp_path / "backups" / "silence.mp3"
    assert backup_path.exists()
    assert backup_path.read_bytes() == original_bytes
    on_disk = read_track(Path(track.path))
    assert on_disk.title == "New Title"  # the live file still got the real edit


def test_apply_without_backup_store_backs_up_nothing(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs_id = _stage_title_edit(db_session, track, "New Title")

    result = apply_changeset(db_session, cs_id, backup_store=None)
    db_session.commit()

    assert result.state == "applied"
    assert not (tmp_path / "backups").exists()


def test_apply_with_backup_store_still_backs_up_when_content_hash_is_null(
    db_session: Session, tmp_path: Path
) -> None:
    """Regression test for §11m (docs/product-spec.md): content_hash is null
    on a Track row only when the file's tags failed to read at the
    last scan (pipeline/scan.py sets it unconditionally on every
    successful probe) — never deferred for cost reasons. The pre-fix
    code's `and track.content_hash is not None` guard silently skipped
    the backup for such a track while still reporting the apply as
    successful, which changes/backup.py's own docstring calls worse
    than no backup feature at all. Fixed by recomputing the hash fresh
    at apply time rather than trusting the stale/absent stored value."""
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    original_bytes = (library / "silence.mp3").read_bytes()
    backup_store = BackupStore(tmp_path / "backups", library_root=library)

    # Simulate the only real path to a null content_hash: the track's
    # tags failed to read at some past scan (probe_error set, content_hash
    # never populated). The file itself is fine now — read_track above
    # already proved that when _scan_one ran a real scan — matching the
    # realistic case of a track that failed to parse once and is fine now.
    track.content_hash = None
    db_session.commit()

    cs_id = _stage_title_edit(db_session, track, "New Title")
    result = apply_changeset(db_session, cs_id, backup_store=backup_store)
    db_session.commit()

    assert result.state == "applied"
    backup_path = tmp_path / "backups" / "silence.mp3"
    assert backup_path.exists(), "backup must not be silently skipped when content_hash is null"
    assert backup_path.read_bytes() == original_bytes


def test_apply_backup_uses_recomputed_hash_on_a_second_changeset(
    db_session: Session, tmp_path: Path
) -> None:
    """A second apply backs up the first applied version under its current hash.

    Apply now realigns catalog stat/hash facts immediately, so BackupStore must observe
    the changed hash instead of relying on the stale pre-apply value that used to make
    this test pass for the wrong reason.
    """
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    backup_store = BackupStore(tmp_path / "backups", library_root=library)

    cs_id_1 = _stage_title_edit(db_session, track, "First Title")
    apply_changeset(db_session, cs_id_1, backup_store=backup_store)
    db_session.commit()

    live_path = Path(track.path)
    first_applied_bytes = live_path.read_bytes()
    first_applied_stat = live_path.stat()
    db_session.refresh(track)
    assert track.content_hash == partial_content_hash(
        live_path, first_applied_stat.st_size
    )

    backup_path = tmp_path / "backups" / "silence.mp3"
    tampered = b"if this survives, backup() re-copied incorrectly"
    backup_path.write_bytes(tampered)

    cs_id_2 = _stage_title_edit(db_session, track, "Second Title")
    apply_changeset(db_session, cs_id_2, backup_store=backup_store)
    db_session.commit()

    assert backup_path.read_bytes() == first_applied_bytes
    assert backup_path.read_bytes() != tampered


def test_apply_backup_failure_marks_change_failed_and_does_not_write(
    db_session: Session, tmp_path: Path
) -> None:
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    original_bytes = (library / "silence.mp3").read_bytes()

    # backup_dir is a file, not a directory -> mkdir(parents=True) inside
    # BackupStore.backup() raises OSError, which surfaces as BackupError.
    unusable_backup_root = tmp_path / "backups_is_a_file"
    unusable_backup_root.write_bytes(b"not a directory")
    backup_store = BackupStore(unusable_backup_root, library_root=library)

    cs_id = _stage_title_edit(db_session, track, "New Title")
    result = apply_changeset(db_session, cs_id, backup_store=backup_store)
    db_session.commit()

    assert result.state == "failed"
    assert track.id in result.conflicted_track_ids
    on_disk_bytes = (library / "silence.mp3").read_bytes()
    assert on_disk_bytes == original_bytes  # never wrote the tag edit
