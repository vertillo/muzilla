from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.changes.applier import apply_changeset
from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.changes.undo import build_undo_changeset
from muzilla.db.models import ApplyJournal, ChangeSet, Track
from muzilla.pipeline.scan import scan_library
from muzilla.tags.reader import read_lyrics, read_track

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"

_LYRICS_TEXT = "Verse one\nVerse two"


def _scan_one(db_session: Session, tmp_path: Path, name: str = "silence.mp3") -> Track:
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / name, library / name)
    scan_library(db_session, library)
    db_session.commit()
    return db_session.query(Track).filter(Track.filename == name).one()


def _stage_write_lyrics(
    db_session: Session, track: Track, payload: dict[str, object] | None, *, title: str = "Lyrics"
) -> ChangeSet:
    cs = build_changeset(
        db_session,
        title=title,
        source="enrichment",
        edits={track.id: [FieldEdit(field="lyrics", new_value=payload, op="write_lyrics")]},
    )
    db_session.commit()
    return cs


def test_apply_writes_lyrics_and_sets_track_columns(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    assert track.has_lyrics is False
    assert track.lyrics_synced is False

    cs = _stage_write_lyrics(db_session, track, {"text": _LYRICS_TEXT, "synced": False})

    result = apply_changeset(db_session, cs.id)
    db_session.commit()

    assert result.state == "applied"
    db_session.refresh(track)
    assert track.has_lyrics is True
    assert track.lyrics_synced is False

    on_disk_text = read_lyrics(Path(track.path))
    assert on_disk_text == _LYRICS_TEXT
    on_disk_meta = read_track(Path(track.path))
    assert on_disk_meta.has_lyrics is True


def test_apply_synced_lyrics_sets_lyrics_synced_true(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = _stage_write_lyrics(db_session, track, {"text": "[00:01.00]synced line", "synced": True})

    apply_changeset(db_session, cs.id)
    db_session.commit()
    db_session.refresh(track)

    assert track.has_lyrics is True
    assert track.lyrics_synced is True


def test_apply_preserves_existing_tags(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    original_title = track.title

    cs = _stage_write_lyrics(db_session, track, {"text": _LYRICS_TEXT, "synced": False})
    apply_changeset(db_session, cs.id)
    db_session.commit()

    on_disk = read_track(Path(track.path))
    assert on_disk.title == original_title


def test_clearing_lyrics_removes_value_and_updates_track(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs1 = _stage_write_lyrics(db_session, track, {"text": _LYRICS_TEXT, "synced": False})
    apply_changeset(db_session, cs1.id)
    db_session.commit()
    db_session.refresh(track)
    assert track.has_lyrics is True

    cs2 = _stage_write_lyrics(db_session, track, None, title="Clear lyrics")
    apply_changeset(db_session, cs2.id)
    db_session.commit()
    db_session.refresh(track)

    assert track.has_lyrics is False
    assert track.lyrics_synced is False
    assert read_lyrics(Path(track.path)) is None


def test_undo_of_write_lyrics_restores_previous_state(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = _stage_write_lyrics(db_session, track, {"text": _LYRICS_TEXT, "synced": False})
    apply_changeset(db_session, cs.id)
    db_session.commit()
    db_session.refresh(track)
    assert track.has_lyrics is True

    undo_cs = build_undo_changeset(db_session, cs.id)
    db_session.commit()
    undo_result = apply_changeset(db_session, undo_cs.id)
    db_session.commit()

    assert undo_result.state == "applied"
    db_session.refresh(track)
    assert track.has_lyrics is False
    assert read_lyrics(Path(track.path)) is None


def test_apply_journal_records_tags_phase_for_lyrics_only_change(
    db_session: Session, tmp_path: Path
) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = _stage_write_lyrics(db_session, track, {"text": _LYRICS_TEXT, "synced": False})

    apply_changeset(db_session, cs.id)
    db_session.commit()

    journal_rows = db_session.query(ApplyJournal).filter(ApplyJournal.change_set_id == cs.id).all()
    assert len(journal_rows) == 1
    assert journal_rows[0].phase == "tags"
    assert journal_rows[0].state == "done"
