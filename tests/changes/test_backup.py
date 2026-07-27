from __future__ import annotations

from pathlib import Path

from muzilla.changes.backup import BackupStore


def _make_library(tmp_path: Path) -> Path:
    library = tmp_path / "library"
    (library / "Artist").mkdir(parents=True)
    (library / "Artist" / "foo.mp3").write_bytes(b"original bytes")
    return library


def test_backup_copies_file_preserving_relative_layout(tmp_path: Path) -> None:
    library = _make_library(tmp_path)
    backup_root = tmp_path / "backups"
    store = BackupStore(backup_root, library_root=library)
    source = library / "Artist" / "foo.mp3"

    backup_path = store.backup(source, "hash-1")

    assert backup_path == backup_root / "Artist" / "foo.mp3"
    assert backup_path.read_bytes() == b"original bytes"


def test_backup_is_idempotent_for_same_content_hash(tmp_path: Path) -> None:
    library = _make_library(tmp_path)
    store = BackupStore(tmp_path / "backups", library_root=library)
    source = library / "Artist" / "foo.mp3"

    store.backup(source, "hash-1")
    backup_path = store.backup(source, "hash-1")  # simulates a second apply run
    backup_path.write_bytes(b"CORRUPTED")  # if backup() re-copies, this gets overwritten
    store.backup(source, "hash-1")

    assert backup_path.read_bytes() == b"CORRUPTED"  # proves the second call was a no-op


def test_backup_recopies_when_content_hash_changes(tmp_path: Path) -> None:
    library = _make_library(tmp_path)
    store = BackupStore(tmp_path / "backups", library_root=library)
    source = library / "Artist" / "foo.mp3"

    store.backup(source, "hash-1")
    source.write_bytes(b"new bytes after some earlier apply")
    backup_path = store.backup(source, "hash-2")

    assert backup_path.read_bytes() == b"new bytes after some earlier apply"


def test_already_backed_up_reports_correctly(tmp_path: Path) -> None:
    library = _make_library(tmp_path)
    store = BackupStore(tmp_path / "backups", library_root=library)
    source = library / "Artist" / "foo.mp3"

    assert store.already_backed_up(source, "hash-1") is False
    store.backup(source, "hash-1")
    assert store.already_backed_up(source, "hash-1") is True
    assert store.already_backed_up(source, "hash-2") is False


def test_backup_falls_back_to_flat_name_outside_library_root(tmp_path: Path) -> None:
    library = _make_library(tmp_path)
    store = BackupStore(tmp_path / "backups", library_root=library)
    outside = tmp_path / "elsewhere.mp3"
    outside.write_bytes(b"stray file")

    backup_path = store.backup(outside, "hash-1")

    assert backup_path == tmp_path / "backups" / "elsewhere.mp3"
    assert backup_path.read_bytes() == b"stray file"
