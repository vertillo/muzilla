from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.pipeline.scan import scan_library
from muzilla.services import strip as strip_service

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _scan_one(db_session: Session, tmp_path: Path) -> Track:
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "silence.mp3", library / "a.mp3")
    scan_library(db_session, library)
    db_session.commit()
    return db_session.query(Track).one()


def test_propose_strip_clears_default_strip_fields(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    assert track.comment  # fixture has comment set

    cs = strip_service.propose_strip(db_session, track_ids=[track.id])
    db_session.commit()

    assert cs.source == "strip_tags"
    fields_touched = {c.field for c in cs.changes}
    assert "comment" in fields_touched
    # comment is default_strip=True -> auto-accepted
    comment_change = next(c for c in cs.changes if c.field == "comment")
    assert comment_change.decision == "accepted"
    assert comment_change.severity == "destructive"


def test_propose_strip_no_tracks_raises(db_session: Session) -> None:
    with pytest.raises(ValueError, match="no tracks selected"):
        strip_service.propose_strip(db_session, track_ids=[])


def test_propose_strip_nothing_to_strip_raises(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    track.comment = None
    track.encoder = None
    db_session.commit()

    with pytest.raises(ValueError, match="no strippable"):
        strip_service.propose_strip(db_session, track_ids=[track.id])


def test_propose_strip_accepts_a_field_name_override(db_session: Session, tmp_path: Path) -> None:
    """services/settings.py's strip_fields override flows through this
    parameter — a caller-supplied list replaces
    the registry's built-in default_strip set entirely rather than
    adding to it."""
    track = _scan_one(db_session, tmp_path)
    assert track.title  # fixture has a title; not default_strip normally

    cs = strip_service.propose_strip(db_session, track_ids=[track.id], strip_field_names=["title"])
    db_session.commit()

    fields_touched = {c.field for c in cs.changes}
    assert fields_touched == {"title"}
