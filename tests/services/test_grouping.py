from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.services import changesets as changesets_service
from muzilla.services import grouping as grouping_service


def _make_track(session: Session, *, path: str, **kwargs: object) -> Track:
    t = Track(path=path, filename=path.rsplit("/", 1)[-1], ext=".mp3", size_bytes=1, mtime_ns=1, **kwargs)
    session.add(t)
    session.flush()
    return t


def test_run_cascade_creates_groups(db_session: Session) -> None:
    _make_track(db_session, path="/a1", title="T1", artist="X", album="Al", album_artist="X")
    _make_track(db_session, path="/a2", title="T2", artist="X", album="Al", album_artist="X")
    db_session.commit()

    result = grouping_service.run_cascade(db_session)
    assert result.groups_created >= 1

    groups = grouping_service.list_groups(db_session)
    assert len(groups) >= 1


def test_pin_group_creates_and_applies_changeset(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/b1", title="T1", artist="X", album="Al", album_artist="X")
    _make_track(db_session, path="/b2", title="T2", artist="X", album="Al", album_artist="X")
    db_session.commit()
    grouping_service.run_cascade(db_session)
    db_session.refresh(t1)
    group_id = t1.group_id
    assert group_id is not None

    cs = grouping_service.pin_group(db_session, group_id=group_id)
    db_session.commit()
    assert cs.source == "grouping_correction"
    # grouping_correction changes auto-accept
    assert all(c.decision == "accepted" for c in cs.changes)

    result = changesets_service.apply_now(db_session, cs.id)
    assert result.state == "applied"

    detail = grouping_service.get_group(db_session, group_id)
    assert detail is not None
    assert detail.is_pinned is True


def test_force_to_singleton_pulls_track_out(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/c1", title="T1", artist="X", album="Al", album_artist="X")
    _make_track(db_session, path="/c2", title="T2", artist="X", album="Al", album_artist="X")
    db_session.commit()
    grouping_service.run_cascade(db_session)
    db_session.refresh(t1)
    original_group_id = t1.group_id
    assert original_group_id is not None

    cs = grouping_service.force_to_singleton(db_session, track_id=t1.id)
    db_session.commit()
    changesets_service.apply_now(db_session, cs.id)

    db_session.refresh(t1)
    assert t1.group_id != original_group_id


def test_merge_groups_moves_tracks(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/d1", title="T1", artist="X", album="Al1", album_artist="X")
    t2 = _make_track(db_session, path="/d2", title="T2", artist="Y", album="Al2", album_artist="Y")
    db_session.commit()
    grouping_service.run_cascade(db_session)
    db_session.refresh(t1)
    db_session.refresh(t2)
    group1, group2 = t1.group_id, t2.group_id
    assert group1 != group2

    cs = grouping_service.merge_groups(db_session, into_group_id=group1, from_group_ids=[group2])
    db_session.commit()
    changesets_service.apply_now(db_session, cs.id)

    db_session.refresh(t2)
    assert t2.group_id == group1


def test_merge_into_self_raises(db_session: Session) -> None:
    with pytest.raises(ValueError, match="cannot merge"):
        grouping_service.merge_groups(db_session, into_group_id=1, from_group_ids=[1])
