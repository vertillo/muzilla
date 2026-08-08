from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from muzilla.changes.applier import recover_apply_journal
from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.db.models import ApplyJournal, ChangeSet, Track
from muzilla.pipeline.scan import scan_library
from muzilla.tags.reader import read_track
from muzilla.tags.writer import write_fields

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _scan_one(db_session: Session, tmp_path: Path, name: str = "silence.mp3") -> Track:
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / name, library / name)
    scan_library(db_session, library)
    db_session.commit()
    return db_session.query(Track).filter(Track.filename == name).one()


def _staged_changeset(db_session: Session, track: Track) -> ChangeSet:
    cs = build_changeset(
        db_session,
        title="Manual edit",
        source="manual_edit",
        edits={track.id: [FieldEdit(field="title", new_value="Interrupted Title", is_manual=True)]},
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()
    return cs


def test_recovery_reverts_journal_when_write_never_landed(
    db_session: Session, tmp_path: Path
) -> None:
    """Simulates a crash between journal PENDING and the tmp-write —
    the file still has its original bytes, so recovery should just
    mark the journal reverted with no file mutation needed."""
    track = _scan_one(db_session, tmp_path)
    original_title = track.title
    cs = _staged_changeset(db_session, track)

    journal = ApplyJournal(
        change_set_id=cs.id,
        track_id=track.id,
        path=track.path,
        phase="tags",
        state="pending",
        before_hash=track.tag_hash,
        before_blob={"title": original_title},
    )
    db_session.add(journal)
    db_session.commit()

    report = recover_apply_journal(db_session)

    assert report.reverted == 1
    db_session.expire_all()
    refreshed_journal = db_session.get(ApplyJournal, journal.id)
    assert refreshed_journal is not None
    assert refreshed_journal.state == "reverted"
    on_disk = read_track(Path(track.path))
    assert on_disk.title == original_title


def test_recovery_confirms_done_when_write_already_completed(
    db_session: Session, tmp_path: Path
) -> None:
    """Simulates a crash after the file write completed but before the
    journal/changeset bookkeeping caught up — recovery should leave the
    file alone and just mark the journal done."""
    from muzilla.domain.metadata import tag_hash as compute_tag_hash

    track = _scan_one(db_session, tmp_path)
    original_title = track.title
    cs = _staged_changeset(db_session, track)

    write_fields(Path(track.path), {"title": "Interrupted Title"})
    after_meta = read_track(Path(track.path))
    after_hash = compute_tag_hash(after_meta)

    journal = ApplyJournal(
        change_set_id=cs.id,
        track_id=track.id,
        path=track.path,
        phase="tags",
        state="writing",
        before_hash=track.tag_hash,
        after_hash=after_hash,
        before_blob={"title": original_title},
    )
    db_session.add(journal)
    db_session.commit()

    report = recover_apply_journal(db_session)

    assert report.confirmed_done == 1
    db_session.expire_all()
    refreshed_journal = db_session.get(ApplyJournal, journal.id)
    assert refreshed_journal is not None
    assert refreshed_journal.state == "done"
    on_disk = read_track(Path(track.path))
    assert on_disk.title == "Interrupted Title"  # untouched, the write already succeeded


def test_recovery_restores_from_before_blob_when_indeterminate(
    db_session: Session, tmp_path: Path
) -> None:
    """Simulates a crash mid-write where the on-disk state matches
    neither before_hash nor after_hash (e.g. write landed but after_hash
    was never recorded) — recovery must restore the original tags and
    flag the owning changeset as failed for re-review."""
    track = _scan_one(db_session, tmp_path)
    original_title = track.title
    cs = _staged_changeset(db_session, track)

    write_fields(Path(track.path), {"title": "Interrupted Title"})

    journal = ApplyJournal(
        change_set_id=cs.id,
        track_id=track.id,
        path=track.path,
        phase="tags",
        state="writing",
        before_hash=track.tag_hash,
        after_hash=None,  # never recorded -- the crash landed here
        before_blob={"title": original_title},
    )
    db_session.add(journal)
    db_session.commit()

    report = recover_apply_journal(db_session)

    assert report.reverted == 1
    db_session.expire_all()
    refreshed_journal = db_session.get(ApplyJournal, journal.id)
    assert refreshed_journal is not None
    assert refreshed_journal.state == "reverted"

    on_disk = read_track(Path(track.path))
    assert on_disk.title == original_title

    refreshed_cs = db_session.get(ChangeSet, cs.id)
    assert refreshed_cs is not None
    assert refreshed_cs.state == "failed"
    assert refreshed_cs.error is not None


def test_recovery_never_reports_reverted_when_restore_fails(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = _staged_changeset(db_session, track)
    write_fields(Path(track.path), {"title": "Indeterminate Title"})
    journal = ApplyJournal(
        change_set_id=cs.id,
        track_id=track.id,
        path=track.path,
        phase="tags",
        state="writing",
        before_hash=track.tag_hash,
        after_hash=None,
        before_blob={"title": track.title},
    )
    db_session.add(journal)
    db_session.commit()

    def fail_restore(*args: object, **kwargs: object) -> None:
        raise OSError("injected restore failure")

    monkeypatch.setattr("muzilla.changes.applier._restore_from_before_blob", fail_restore)

    report = recover_apply_journal(db_session)

    assert report.reverted == 0
    assert report.failed == 1
    db_session.refresh(journal)
    assert journal.state == "failed"
    assert "restore failed" in (journal.error or "")


def test_recovery_ignores_journals_already_terminal(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = _staged_changeset(db_session, track)

    journal = ApplyJournal(
        change_set_id=cs.id,
        track_id=track.id,
        path=track.path,
        phase="tags",
        state="done",
        before_hash=track.tag_hash,
        after_hash=track.tag_hash,
        before_blob={"title": track.title},
    )
    db_session.add(journal)
    db_session.commit()

    report = recover_apply_journal(db_session)

    assert report.reverted == 0
    assert report.confirmed_done == 0


# --- move-phase recovery -------------------------------------------------------


def _move_journal(
    db_session: Session, cs: ChangeSet, track: Track, *, before_path: str, after_path: str, state: str
) -> ApplyJournal:
    journal = ApplyJournal(
        change_set_id=cs.id,
        track_id=track.id,
        path=track.path,
        phase="move",
        state=state,
        before_path=before_path,
        after_path=after_path,
    )
    db_session.add(journal)
    db_session.commit()
    return journal


def test_move_recovery_reverted_when_move_never_happened(
    db_session: Session, tmp_path: Path
) -> None:
    """before_path exists, after_path doesn't -- the move never
    happened (or was already reverted); nothing to do."""
    track = _scan_one(db_session, tmp_path)
    cs = _staged_changeset(db_session, track)
    library = tmp_path / "library"
    after = library / "moved.mp3"  # never created

    journal = _move_journal(
        db_session, cs, track, before_path=track.path, after_path=str(after), state="writing"
    )

    report = recover_apply_journal(db_session)

    assert report.reverted == 1
    db_session.expire_all()
    refreshed = db_session.get(ApplyJournal, journal.id)
    assert refreshed is not None
    assert refreshed.state == "reverted"
    assert Path(track.path).exists()

    refreshed_cs = db_session.get(ChangeSet, cs.id)
    assert refreshed_cs is not None
    assert refreshed_cs.state != "failed"  # a clean revert, not a review-needed case


def test_move_recovery_confirmed_done_when_move_completed(
    db_session: Session, tmp_path: Path
) -> None:
    """after_path exists, before_path doesn't -- the move completed
    before the crash; only the journal bookkeeping was interrupted."""
    track = _scan_one(db_session, tmp_path)
    cs = _staged_changeset(db_session, track)
    library = tmp_path / "library"
    new_path = library / "moved.mp3"
    original_path = track.path
    Path(original_path).rename(new_path)  # simulate the completed move

    journal = _move_journal(
        db_session, cs, track, before_path=original_path, after_path=str(new_path), state="writing"
    )

    report = recover_apply_journal(db_session)

    assert report.confirmed_done == 1
    db_session.expire_all()
    refreshed = db_session.get(ApplyJournal, journal.id)
    assert refreshed is not None
    assert refreshed.state == "done"
    assert new_path.exists()


def test_move_recovery_failed_when_neither_path_exists(
    db_session: Session, tmp_path: Path
) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = _staged_changeset(db_session, track)
    library = tmp_path / "library"
    original_path = track.path
    Path(original_path).unlink()  # neither before nor after exists

    journal = _move_journal(
        db_session,
        cs,
        track,
        before_path=original_path,
        after_path=str(library / "moved.mp3"),
        state="writing",
    )

    report = recover_apply_journal(db_session)

    assert report.reverted == 0
    assert report.failed == 1
    db_session.expire_all()
    refreshed = db_session.get(ApplyJournal, journal.id)
    assert refreshed is not None
    assert refreshed.state == "failed"
    assert refreshed.error is not None
    assert "neither" in refreshed.error

    refreshed_cs = db_session.get(ChangeSet, cs.id)
    assert refreshed_cs is not None
    assert refreshed_cs.state == "failed"


def test_move_recovery_failed_when_both_paths_exist(
    db_session: Session, tmp_path: Path
) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = _staged_changeset(db_session, track)
    library = tmp_path / "library"
    new_path = library / "moved.mp3"
    shutil.copy(track.path, new_path)  # both now exist

    journal = _move_journal(
        db_session, cs, track, before_path=track.path, after_path=str(new_path), state="writing"
    )

    report = recover_apply_journal(db_session)

    assert report.reverted == 0
    assert report.failed == 1
    db_session.expire_all()
    refreshed = db_session.get(ApplyJournal, journal.id)
    assert refreshed is not None
    assert refreshed.state == "failed"
    assert refreshed.error is not None
    assert "both" in refreshed.error

    refreshed_cs = db_session.get(ChangeSet, cs.id)
    assert refreshed_cs is not None
    assert refreshed_cs.state == "failed"
