from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.pipeline.scan import scan_library
from muzilla.services import changesets as changesets_service
from muzilla.services import edit as edit_service

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _scan_one(db_session: Session, tmp_path: Path) -> Track:
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "silence.mp3", library / "a.mp3")
    scan_library(db_session, library)
    db_session.commit()
    return db_session.query(Track).one()


def test_get_changeset_includes_field_diffs(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"title": "New Title"})
    db_session.commit()

    detail = changesets_service.get_changeset(db_session, cs.id)
    assert detail is not None
    assert len(detail.changes) == 1
    change = detail.changes[0]
    assert change.diff.kind == "text"
    assert change.diff.new_value == "New Title"


def test_get_changeset_missing_returns_none(db_session: Session) -> None:
    assert changesets_service.get_changeset(db_session, 999) is None


def test_apply_decisions_persists_accept_and_manual_edit(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"title": "Draft Title"})
    db_session.commit()
    change_id = cs.changes[0].id

    detail = changesets_service.apply_decisions(
        db_session,
        cs.id,
        [changesets_service.ChangeDecision(change_id=change_id, decision="accepted", new_value="Overridden Title")],
    )
    assert detail.changes[0].decision == "accepted"
    assert detail.changes[0].new_value == "Overridden Title"
    assert detail.changes[0].is_manual is True


def test_apply_decisions_on_non_draft_raises(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"title": "X"})
    db_session.commit()
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()
    changesets_service.apply_now(db_session, cs.id)

    with pytest.raises(ValueError, match="not draft"):
        changesets_service.apply_decisions(
            db_session, cs.id, [changesets_service.ChangeDecision(change_id=cs.changes[0].id, decision="rejected")]
        )


def test_list_changesets_pagination(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    for i in range(3):
        edit_service.edit_track(db_session, track_id=track.id, field_values={"title": f"T{i}"})
    db_session.commit()

    page = changesets_service.list_changesets(db_session, limit=2)
    assert page.total == 3
    assert len(page.items) == 2
    assert page.next_cursor is not None

    page2 = changesets_service.list_changesets(db_session, limit=2, cursor=page.next_cursor)
    assert len(page2.items) == 1


def test_full_apply_undo_roundtrip_via_service(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    original_title = track.title
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"title": "Changed"})
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    result = changesets_service.apply_now(db_session, cs.id)
    assert result.state == "applied"

    undo_detail = changesets_service.undo_now(db_session, cs.id)
    assert undo_detail.source == f"undo_of:{cs.id}"

    changesets_service.apply_now(db_session, undo_detail.id)
    db_session.refresh(track)
    assert track.title == original_title


def test_apply_enqueues_job_and_returns_its_id(db_session: Session, tmp_path: Path) -> None:
    from muzilla.jobs import queue

    track = _scan_one(db_session, tmp_path)
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"title": "Changed"})
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    job_id = changesets_service.apply(db_session, cs.id)
    job = queue.get_job(db_session, job_id)
    assert job is not None
    assert job.type == "apply_changeset"
    assert job.payload == {"change_set_id": cs.id}


def test_undo_enqueues_job_and_returns_its_id(db_session: Session, tmp_path: Path) -> None:
    from muzilla.jobs import queue

    track = _scan_one(db_session, tmp_path)
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"title": "Changed"})
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()
    changesets_service.apply_now(db_session, cs.id)

    job_id = changesets_service.undo(db_session, cs.id)
    job = queue.get_job(db_session, job_id)
    assert job is not None
    assert job.type == "undo_changeset"
    assert job.payload == {"change_set_id": cs.id}


def test_recover_apply_journal_delegates_to_changes_applier(
    db_session: Session, tmp_path: Path
) -> None:
    # No stuck journal rows in a fresh DB -- confirms the service
    # wrapper is wired correctly rather than duplicating the logic.
    report = changesets_service.recover_apply_journal(db_session)
    assert report.reverted == 0
    assert report.confirmed_done == 0
