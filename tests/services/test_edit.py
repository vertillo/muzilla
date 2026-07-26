from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.pipeline.scan import scan_library
from muzilla.services import edit as edit_service
from muzilla.services.changesets import apply_now as apply_changeset

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _scan_two(db_session: Session, tmp_path: Path) -> list[Track]:
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "silence.mp3", library / "a.mp3")
    shutil.copy(FIXTURES / "silence.flac", library / "b.flac")
    scan_library(db_session, library)
    db_session.commit()
    return list(db_session.query(Track).order_by(Track.id).all())


def test_edit_track_rejects_unknown_field(db_session: Session, tmp_path: Path) -> None:
    tracks = _scan_two(db_session, tmp_path)
    with pytest.raises(edit_service.EditValidationError):
        edit_service.edit_track(db_session, track_id=tracks[0].id, field_values={"nonsense": "x"})


def test_edit_track_rejects_readonly_field(db_session: Session, tmp_path: Path) -> None:
    tracks = _scan_two(db_session, tmp_path)
    with pytest.raises(edit_service.EditValidationError):
        edit_service.edit_track(db_session, track_id=tracks[0].id, field_values={"bitrate": 320})


def test_edit_track_builds_draft_changeset(db_session: Session, tmp_path: Path) -> None:
    tracks = _scan_two(db_session, tmp_path)
    cs = edit_service.edit_track(
        db_session, track_id=tracks[0].id, field_values={"title": "New Title", "year": 2020}
    )
    db_session.commit()

    assert cs.state == "draft"
    assert cs.source == "manual_edit"
    assert len(cs.changes) == 2
    assert all(c.is_manual for c in cs.changes)


def test_bulk_edit_applies_same_field_to_all_selected(db_session: Session, tmp_path: Path) -> None:
    tracks = _scan_two(db_session, tmp_path)
    cs = edit_service.edit_tracks_bulk(
        db_session,
        track_ids=[t.id for t in tracks],
        field_values=[edit_service.BulkEditField(field="album_artist", new_value="Various Artists")],
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    result = apply_changeset(db_session, cs.id)
    assert result.state == "applied"
    for t in tracks:
        db_session.refresh(t)
        assert t.album_artist == "Various Artists"


def test_bulk_edit_no_tracks_raises(db_session: Session) -> None:
    with pytest.raises(edit_service.EditValidationError):
        edit_service.edit_tracks_bulk(db_session, track_ids=[], field_values=[])


def test_find_replace_preview_and_apply(db_session: Session, tmp_path: Path) -> None:
    tracks = _scan_two(db_session, tmp_path)
    for t in tracks:
        t.comment = "Ripped by EvilRipper2008"
    db_session.commit()

    preview = edit_service.preview_find_replace(
        db_session,
        track_ids=[t.id for t in tracks],
        field="comment",
        find="EvilRipper2008",
        replace="",
    )
    assert len(preview) == 2
    assert all(row.new_value == "Ripped by " for row in preview)

    cs = edit_service.apply_find_replace(
        db_session,
        track_ids=[t.id for t in tracks],
        field="comment",
        find="EvilRipper2008",
        replace="",
    )
    db_session.commit()
    assert len(cs.changes) == 2
    assert cs.source == "manual_edit"


def test_find_replace_no_matches_raises(db_session: Session, tmp_path: Path) -> None:
    tracks = _scan_two(db_session, tmp_path)
    with pytest.raises(edit_service.EditValidationError):
        edit_service.apply_find_replace(
            db_session,
            track_ids=[t.id for t in tracks],
            field="comment",
            find="ThisStringDoesNotExistAnywhere",
            replace="x",
        )


def test_find_replace_regex_opt_in(db_session: Session, tmp_path: Path) -> None:
    tracks = _scan_two(db_session, tmp_path)
    for t in tracks:
        t.comment = "Track 001 ripped"
    db_session.commit()

    preview = edit_service.preview_find_replace(
        db_session,
        track_ids=[t.id for t in tracks],
        field="comment",
        find=r"\d+",
        replace="###",
        use_regex=True,
    )
    assert all("###" in row.new_value for row in preview)
