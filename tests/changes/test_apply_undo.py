from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.changes.applier import apply_changeset
from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.changes.undo import build_undo_changeset
from muzilla.db.models import ApplyJournal, Change, ChangeSet, Track
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


def test_build_apply_updates_file_and_track(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    original_title = track.title

    cs = build_changeset(
        db_session,
        title="Manual edit",
        source="manual_edit",
        edits={track.id: [FieldEdit(field="title", new_value="Brand New Title", is_manual=True)]},
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    result = apply_changeset(db_session, cs.id)
    db_session.commit()

    assert result.state == "applied"
    assert track.id in result.applied_track_ids

    db_session.refresh(track)
    assert track.title == "Brand New Title"
    assert track.title != original_title

    on_disk = read_track(Path(track.path))
    assert on_disk.title == "Brand New Title"

    cs_row = db_session.get(ChangeSet, cs.id)
    assert cs_row is not None
    assert cs_row.state == "applied"

    journal_rows = db_session.query(ApplyJournal).filter(ApplyJournal.change_set_id == cs.id).all()
    assert len(journal_rows) == 1
    assert journal_rows[0].state == "done"
    assert journal_rows[0].before_blob["title"] == original_title


def test_rejected_change_never_touches_disk(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    original_title = track.title

    cs = build_changeset(
        db_session,
        title="Manual edit",
        source="manual_edit",
        edits={track.id: [FieldEdit(field="title", new_value="Should Not Apply")]},
    )
    for c in cs.changes:
        c.decision = "rejected"
    db_session.commit()

    result = apply_changeset(db_session, cs.id)
    db_session.commit()

    assert result.state == "applied"  # nothing accepted -> trivially "applied" (no-op)
    on_disk = read_track(Path(track.path))
    assert on_disk.title == original_title


def test_conflict_detected_when_file_modified_since_staging(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)

    cs = build_changeset(
        db_session,
        title="Manual edit",
        source="manual_edit",
        edits={track.id: [FieldEdit(field="title", new_value="Staged Title")]},
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    # Simulate an external tool (Picard) editing the file after staging.
    from muzilla.tags.writer import write_fields

    write_fields(track.path, {"title": "Edited By Picard"})

    result = apply_changeset(db_session, cs.id)
    db_session.commit()

    assert result.state == "failed"
    assert track.id in result.conflicted_track_ids

    change_row = db_session.query(Change).filter(Change.change_set_id == cs.id).one()
    assert change_row.apply_state == "conflicted"

    on_disk = read_track(Path(track.path))
    assert on_disk.title == "Edited By Picard"  # untouched by the aborted write


def test_undo_restores_original_value(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    original_title = track.title

    cs = build_changeset(
        db_session,
        title="Manual edit",
        source="manual_edit",
        edits={track.id: [FieldEdit(field="title", new_value="Changed Title")]},
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()
    apply_changeset(db_session, cs.id)
    db_session.commit()

    undo_cs = build_undo_changeset(db_session, cs.id)
    db_session.commit()
    assert undo_cs.source == f"undo_of:{cs.id}"
    assert all(c.decision == "accepted" for c in undo_cs.changes)

    undo_result = apply_changeset(db_session, undo_cs.id)
    db_session.commit()

    assert undo_result.state == "applied"
    db_session.refresh(track)
    assert track.title == original_title

    on_disk = read_track(Path(track.path))
    assert on_disk.title == original_title


def test_destructive_severity_flagged_on_clear(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    assert track.comment  # fixture has a comment tag set

    cs = build_changeset(
        db_session,
        title="Strip comment",
        source="strip_tags",
        edits={track.id: [FieldEdit(field="comment", new_value=None, op="strip")]},
    )
    db_session.commit()

    change = cs.changes[0]
    assert change.severity == "destructive"
    # strip_tags source auto-accepts default-strip fields (comment is one)
    assert change.decision == "accepted"
