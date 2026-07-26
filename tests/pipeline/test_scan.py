from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.pipeline.scan import scan_library

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _copy_fixtures(dest: Path, names: list[str]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy(FIXTURES / name, dest / name)


def test_scan_adds_new_tracks(db_session: Session, tmp_path: Path) -> None:
    library = tmp_path / "library"
    _copy_fixtures(library, ["silence.mp3", "silence.flac"])

    stats = scan_library(db_session, library)

    assert stats.scanned == 2
    assert stats.added == 2
    assert stats.updated == 0
    assert stats.errored == 0

    tracks = list(db_session.scalars(select(Track)))
    assert len(tracks) == 2
    assert {t.ext for t in tracks} == {".mp3", ".flac"}
    for t in tracks:
        assert t.content_hash is not None
        assert t.tag_hash is not None
        assert t.probe_error is None


def test_rescan_unchanged_library_is_fast_path(db_session: Session, tmp_path: Path) -> None:
    library = tmp_path / "library"
    _copy_fixtures(library, ["silence.mp3"])

    scan_library(db_session, library)
    stats = scan_library(db_session, library)

    assert stats.scanned == 1
    assert stats.unchanged == 1
    assert stats.added == 0
    assert stats.updated == 0


def test_rescan_after_tag_edit_updates_row(db_session: Session, tmp_path: Path) -> None:
    library = tmp_path / "library"
    _copy_fixtures(library, ["silence.mp3"])

    scan_library(db_session, library)

    # Simulate an external edit: bump mtime so the fast path doesn't apply.
    target = library / "silence.mp3"
    import os
    import time

    time.sleep(0.01)
    os.utime(target, None)

    stats = scan_library(db_session, library)

    assert stats.updated == 1
    assert stats.added == 0


def test_scan_marks_vanished_files_missing_without_deleting(
    db_session: Session, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    _copy_fixtures(library, ["silence.mp3", "silence.flac"])

    scan_library(db_session, library)

    (library / "silence.flac").unlink()
    stats = scan_library(db_session, library)

    assert stats.missing == 1
    tracks = list(db_session.scalars(select(Track)))
    assert len(tracks) == 2
    missing = [t for t in tracks if t.ext == ".flac"]
    assert len(missing) == 1
    assert missing[0].missing_since is not None


def test_scan_continues_past_corrupt_file(db_session: Session, tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "good.mp3").write_bytes((FIXTURES / "silence.mp3").read_bytes())
    (library / "bad.mp3").write_bytes(b"not an audio file")

    stats = scan_library(db_session, library)

    assert stats.scanned == 2
    assert stats.errored == 1
    assert stats.added == 1

    tracks = {t.filename: t for t in db_session.scalars(select(Track))}
    assert tracks["bad.mp3"].probe_error is not None
    assert tracks["good.mp3"].probe_error is None


def test_scan_ignores_non_audio_files(db_session: Session, tmp_path: Path) -> None:
    library = tmp_path / "library"
    _copy_fixtures(library, ["silence.mp3"])
    (library / "cover.jpg").write_bytes(b"\xff\xd8\xff")
    (library / "notes.txt").write_text("hello")

    stats = scan_library(db_session, library)

    assert stats.scanned == 1
    tracks = list(db_session.scalars(select(Track)))
    assert len(tracks) == 1
