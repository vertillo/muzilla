from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.changes.applier import apply_changeset
from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.db.models import ApplyJournal, Track
from muzilla.pipeline.scan import scan_library
from muzilla.tags.reader import read_track

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _scan_one(db_session: Session, tmp_path: Path, name: str = "silence.mp3") -> Track:
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / name, library / name)
    scan_library(db_session, library)
    db_session.commit()
    return db_session.query(Track).filter(Track.filename == name).one()


def _stage_move(db_session: Session, track: Track, new_path: str) -> int:
    cs = build_changeset(
        db_session,
        title="Rename",
        source="rename",
        edits={track.id: [FieldEdit(field="path", new_value=new_path, op="move")]},
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()
    return cs.id


def test_move_applies_and_updates_track(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    new_path = str(library / "renamed.mp3")

    cs_id = _stage_move(db_session, track, new_path)
    result = apply_changeset(db_session, cs_id, library_root=library, create_directories=False)
    db_session.commit()

    assert result.state == "applied"
    db_session.refresh(track)
    assert track.path == new_path
    assert track.filename == "renamed.mp3"
    assert Path(new_path).exists()
    assert not (library / "silence.mp3").exists()


def test_move_preserves_tag_content(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    original_title = track.title
    library = tmp_path / "library"
    new_path = str(library / "renamed.mp3")

    cs_id = _stage_move(db_session, track, new_path)
    apply_changeset(db_session, cs_id, library_root=library, create_directories=False)
    db_session.commit()

    on_disk = read_track(Path(new_path))
    assert on_disk.title == original_title


def test_move_creates_directory_when_enabled(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    new_path = str(library / "SubDir" / "renamed.mp3")

    cs_id = _stage_move(db_session, track, new_path)
    result = apply_changeset(db_session, cs_id, library_root=library, create_directories=True)
    db_session.commit()

    assert result.state == "applied"
    assert Path(new_path).exists()


def test_move_journal_row_shape(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    original_path = track.path
    new_path = str(library / "renamed.mp3")

    cs_id = _stage_move(db_session, track, new_path)
    apply_changeset(db_session, cs_id, library_root=library, create_directories=False)
    db_session.commit()

    journal_rows = (
        db_session.query(ApplyJournal)
        .filter(ApplyJournal.change_set_id == cs_id, ApplyJournal.phase == "move")
        .all()
    )
    assert len(journal_rows) == 1
    assert journal_rows[0].state == "done"
    assert journal_rows[0].before_path == original_path
    assert journal_rows[0].after_path == new_path


def test_move_outside_library_root_refused(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    new_path = str(outside_dir / "escaped.mp3")

    cs_id = _stage_move(db_session, track, new_path)
    result = apply_changeset(db_session, cs_id, library_root=library, create_directories=False)
    db_session.commit()

    assert result.state == "failed"
    db_session.refresh(track)
    assert track.path != new_path
    assert not Path(new_path).exists()


def test_move_onto_existing_file_fails_cleanly(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    occupied = library / "occupied.mp3"
    shutil.copy(FIXTURES / "silence.mp3", occupied)
    occupied_bytes = occupied.read_bytes()

    cs_id = _stage_move(db_session, track, str(occupied))
    # A file can appear after preview/review, so the writer must repeat the collision
    # check immediately before the move instead of trusting staging-time validation.
    result = apply_changeset(db_session, cs_id, library_root=library, create_directories=False)
    db_session.commit()
    assert result.state == "failed"
    assert occupied.read_bytes() == occupied_bytes
    assert Path(track.path).exists()


def test_combined_tag_edit_and_move_in_one_changeset(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    new_path = str(library / "renamed.mp3")

    cs = build_changeset(
        db_session,
        title="Edit + rename",
        source="manual_edit",
        edits={
            track.id: [
                FieldEdit(field="title", new_value="New Title", is_manual=True),
                FieldEdit(field="path", new_value=new_path, op="move"),
            ]
        },
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    result = apply_changeset(db_session, cs.id, library_root=library, create_directories=False)
    db_session.commit()

    assert result.state == "applied"
    db_session.refresh(track)
    assert track.path == new_path
    assert track.title == "New Title"
    on_disk = read_track(Path(new_path))
    assert on_disk.title == "New Title"


def test_empty_source_dir_pruned_after_move(db_session: Session, tmp_path: Path) -> None:
    library = tmp_path / "library"
    subdir = library / "OldDir"
    subdir.mkdir(parents=True)
    shutil.copy(FIXTURES / "silence.mp3", subdir / "silence.mp3")
    scan_library(db_session, subdir)
    db_session.commit()
    track = db_session.query(Track).filter(Track.filename == "silence.mp3").one()

    new_path = str(library / "NewDir" / "silence.mp3")
    cs_id = _stage_move(db_session, track, new_path)
    apply_changeset(db_session, cs_id, library_root=library, create_directories=True)
    db_session.commit()

    assert not subdir.exists()
    assert Path(new_path).exists()


def test_pruning_stops_at_still_populated_parent(db_session: Session, tmp_path: Path) -> None:
    library = tmp_path / "library"
    subdir = library / "SharedDir"
    subdir.mkdir(parents=True)
    shutil.copy(FIXTURES / "silence.mp3", subdir / "a.mp3")
    shutil.copy(FIXTURES / "silence.flac", subdir / "b.flac")
    scan_library(db_session, subdir)
    db_session.commit()
    track = db_session.query(Track).filter(Track.filename == "a.mp3").one()

    new_path = str(library / "NewDir" / "a.mp3")
    cs_id = _stage_move(db_session, track, new_path)
    apply_changeset(db_session, cs_id, library_root=library, create_directories=True)
    db_session.commit()

    assert subdir.exists()  # b.flac still lives there
    assert (subdir / "b.flac").exists()


def test_pruning_never_removes_library_root(db_session: Session, tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir(parents=True)
    shutil.copy(FIXTURES / "silence.mp3", library / "a.mp3")
    scan_library(db_session, library)
    db_session.commit()
    track = db_session.query(Track).filter(Track.filename == "a.mp3").one()

    new_path = str(library / "NewDir" / "a.mp3")
    cs_id = _stage_move(db_session, track, new_path)
    apply_changeset(db_session, cs_id, library_root=library, create_directories=True)
    db_session.commit()

    assert library.exists()
