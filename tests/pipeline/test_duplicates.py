from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from muzilla.db.models import DuplicateGroup, DuplicateMember, Track, TrackFingerprintMatch
from muzilla.pipeline.duplicates import detect_duplicates


def _make_track(session: Session, *, path: str, title: str) -> Track:
    t = Track(
        path=path,
        filename=path.rsplit("/", 1)[-1],
        ext=".mp3",
        size_bytes=1000,
        mtime_ns=1,
        title=title,
    )
    session.add(t)
    session.flush()
    return t


def _add_match(session: Session, track: Track, mb_recording_id: str, score: float = 0.9) -> None:
    session.add(
        TrackFingerprintMatch(
            track_id=track.id, mb_recording_id=mb_recording_id, mb_release_ids=[], score=score
        )
    )
    session.flush()


def test_detect_duplicates_groups_tracks_sharing_recording_id(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/a-128kbps.mp3", title="Song")
    t2 = _make_track(db_session, path="/a-320kbps.mp3", title="Song")
    db_session.commit()

    _add_match(db_session, t1, "rec-1")
    _add_match(db_session, t2, "rec-1")
    db_session.commit()

    result = detect_duplicates(db_session)
    db_session.commit()

    assert result.groups_created == 1
    group = db_session.query(DuplicateGroup).filter_by(mb_recording_id="rec-1").one()
    assert group.basis == "acoustid"
    assert group.dismissed is False
    assert {m.track_id for m in group.members} == {t1.id, t2.id}


def test_detect_duplicates_ignores_single_track_recordings(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/a.mp3", title="Song")
    db_session.commit()
    _add_match(db_session, t1, "rec-1")
    db_session.commit()

    result = detect_duplicates(db_session)
    db_session.commit()

    assert result.groups_created == 0
    assert db_session.query(DuplicateGroup).count() == 0


def test_detect_duplicates_picks_highest_scoring_match_per_track(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/a.mp3", title="Song")
    t2 = _make_track(db_session, path="/b.mp3", title="Song")
    db_session.commit()

    # t1 has two candidate recordings; only the higher-scored one counts.
    _add_match(db_session, t1, "rec-low", score=0.3)
    _add_match(db_session, t1, "rec-high", score=0.95)
    _add_match(db_session, t2, "rec-high", score=0.9)
    db_session.commit()

    result = detect_duplicates(db_session)
    db_session.commit()

    assert result.groups_created == 1
    group = db_session.query(DuplicateGroup).filter_by(mb_recording_id="rec-high").one()
    assert {m.track_id for m in group.members} == {t1.id, t2.id}


def test_detect_duplicates_excludes_missing_tracks(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/a.mp3", title="Song")
    t2 = _make_track(db_session, path="/b.mp3", title="Song")
    t2.missing_since = datetime.now(UTC)
    db_session.commit()
    _add_match(db_session, t1, "rec-1")
    _add_match(db_session, t2, "rec-1")
    db_session.commit()

    result = detect_duplicates(db_session)
    db_session.commit()

    assert result.groups_created == 0


def test_detect_duplicates_is_idempotent(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/a.mp3", title="Song")
    t2 = _make_track(db_session, path="/b.mp3", title="Song")
    db_session.commit()
    _add_match(db_session, t1, "rec-1")
    _add_match(db_session, t2, "rec-1")
    db_session.commit()

    first = detect_duplicates(db_session)
    db_session.commit()
    second = detect_duplicates(db_session)
    db_session.commit()

    assert first.groups_created == 1
    assert second.groups_created == 0
    assert second.groups_updated == 1
    assert db_session.query(DuplicateGroup).count() == 1


def test_detect_duplicates_skips_dismissed_groups(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/a.mp3", title="Song")
    t2 = _make_track(db_session, path="/b.mp3", title="Song")
    db_session.commit()
    _add_match(db_session, t1, "rec-1")
    _add_match(db_session, t2, "rec-1")
    db_session.commit()

    detect_duplicates(db_session)
    db_session.commit()
    group = db_session.query(DuplicateGroup).filter_by(mb_recording_id="rec-1").one()
    group.dismissed = True
    db_session.commit()

    result = detect_duplicates(db_session)
    db_session.commit()

    assert result.groups_dismissed_skipped == 1
    assert result.groups_updated == 0
    db_session.refresh(group)
    assert group.dismissed is True


def test_detect_duplicates_removes_group_when_it_drops_below_two_tracks(
    db_session: Session,
) -> None:
    t1 = _make_track(db_session, path="/a.mp3", title="Song")
    t2 = _make_track(db_session, path="/b.mp3", title="Song")
    db_session.commit()
    _add_match(db_session, t1, "rec-1")
    _add_match(db_session, t2, "rec-1")
    db_session.commit()

    detect_duplicates(db_session)
    db_session.commit()
    assert db_session.query(DuplicateGroup).filter_by(mb_recording_id="rec-1").count() == 1

    # t2's fingerprint is re-run and now points somewhere else entirely,
    # leaving only t1 on rec-1 -- no longer a duplicate pair.
    db_session.query(TrackFingerprintMatch).filter_by(track_id=t2.id).delete()
    _add_match(db_session, t2, "rec-2")
    db_session.commit()

    result = detect_duplicates(db_session)
    db_session.commit()

    assert result.groups_removed == 1
    assert db_session.query(DuplicateGroup).filter_by(mb_recording_id="rec-1").count() == 0


def test_detect_duplicates_loads_existing_members_in_one_query(db_session: Session) -> None:
    for index in range(4):
        first = _make_track(db_session, path=f"/{index}-a.mp3", title="Song")
        second = _make_track(db_session, path=f"/{index}-b.mp3", title="Song")
        _add_match(db_session, first, f"rec-{index}")
        _add_match(db_session, second, f"rec-{index}")
    db_session.commit()
    assert detect_duplicates(db_session).groups_created == 4
    db_session.commit()
    db_session.expire_all()

    member_queries: list[str] = []

    def capture_member_query(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if "FROM duplicate_members" in statement:
            member_queries.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_member_query)
    try:
        result = detect_duplicates(db_session)
    finally:
        event.remove(bind, "before_cursor_execute", capture_member_query)

    assert result.groups_updated == 4
    assert len(member_queries) == 1


def test_detect_duplicates_rolls_back_group_inserts_when_member_insert_fails(
    db_session: Session,
) -> None:
    for index in range(4):
        first = _make_track(db_session, path=f"/{index}-a.mp3", title="Song")
        second = _make_track(db_session, path=f"/{index}-b.mp3", title="Song")
        _add_match(db_session, first, f"rec-{index}")
        _add_match(db_session, second, f"rec-{index}")
    db_session.commit()

    def fail_member_insert(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if statement.lstrip().startswith("INSERT INTO duplicate_members"):
            raise RuntimeError("injected duplicate-member insert failure")

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", fail_member_insert)
    try:
        with pytest.raises(RuntimeError, match="injected duplicate-member insert failure"):
            detect_duplicates(db_session)
    finally:
        event.remove(bind, "before_cursor_execute", fail_member_insert)
        db_session.rollback()

    assert db_session.scalar(select(func.count()).select_from(DuplicateGroup)) == 0
    assert db_session.scalar(select(func.count()).select_from(DuplicateMember)) == 0


def test_detect_duplicates_drops_stale_members_when_a_third_track_diverges(
    db_session: Session,
) -> None:
    t1 = _make_track(db_session, path="/a.mp3", title="Song")
    t2 = _make_track(db_session, path="/b.mp3", title="Song")
    t3 = _make_track(db_session, path="/c.mp3", title="Song")
    db_session.commit()
    for t in (t1, t2, t3):
        _add_match(db_session, t, "rec-1")
    db_session.commit()

    detect_duplicates(db_session)
    db_session.commit()

    # t3 diverges, but t1/t2 still share rec-1 -- the group survives,
    # just with t3 removed as a member.
    db_session.query(TrackFingerprintMatch).filter_by(track_id=t3.id).delete()
    _add_match(db_session, t3, "rec-2")
    db_session.commit()

    result = detect_duplicates(db_session)
    db_session.commit()

    assert result.groups_removed == 0
    group = db_session.query(DuplicateGroup).filter_by(mb_recording_id="rec-1").one()
    assert {m.track_id for m in group.members} == {t1.id, t2.id}
