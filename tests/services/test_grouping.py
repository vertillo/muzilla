from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from muzilla.db.models import Track
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


def test_pin_group_applies_immediately(db_session: Session) -> None:
    """Product decision (Phase 7 item 6): pin/merge/split/reassign/
    force-to-singleton auto-apply — the returned ChangeSet is already
    `applied`, not a still-draft changeset the caller must separately
    apply (docs/KNOWN_BUGS.md #3's fix; the bug this regression-tests
    is that clicking Pin used to change nothing observable)."""
    t1 = _make_track(db_session, path="/b1", title="T1", artist="X", album="Al", album_artist="X")
    _make_track(db_session, path="/b2", title="T2", artist="X", album="Al", album_artist="X")
    db_session.commit()
    grouping_service.run_cascade(db_session)
    db_session.refresh(t1)
    group_id = t1.group_id
    assert group_id is not None

    cs = grouping_service.pin_group(db_session, group_id=group_id)

    assert cs.source == "grouping_correction"
    assert cs.state == "applied"
    assert all(c.decision == "accepted" for c in cs.changes)

    detail = grouping_service.get_group(db_session, group_id)
    assert detail is not None
    assert detail.is_pinned is True


def test_force_to_singleton_applies_immediately(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/c1", title="T1", artist="X", album="Al", album_artist="X")
    _make_track(db_session, path="/c2", title="T2", artist="X", album="Al", album_artist="X")
    db_session.commit()
    grouping_service.run_cascade(db_session)
    db_session.refresh(t1)
    original_group_id = t1.group_id
    assert original_group_id is not None

    cs = grouping_service.force_to_singleton(db_session, track_id=t1.id)
    assert cs.state == "applied"

    db_session.refresh(t1)
    assert t1.group_id != original_group_id


def test_merge_groups_applies_immediately(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/d1", title="T1", artist="X", album="Al1", album_artist="X")
    t2 = _make_track(db_session, path="/d2", title="T2", artist="Y", album="Al2", album_artist="Y")
    db_session.commit()
    grouping_service.run_cascade(db_session)
    db_session.refresh(t1)
    db_session.refresh(t2)
    group1, group2 = t1.group_id, t2.group_id
    assert group1 != group2

    cs = grouping_service.merge_groups(db_session, into_group_id=group1, from_group_ids=[group2])
    assert cs.state == "applied"

    db_session.refresh(t2)
    assert t2.group_id == group1


def test_merge_groups_updates_track_count_on_both_sides(db_session: Session) -> None:
    """changes/applier.py's _apply_group_changes never touched
    TrackGroup.track_count before Phase 7 item 6 made these changesets
    actually apply — this is what would otherwise leave both the
    destination group's count too low and the emptied source group's
    count stale at its pre-merge value."""
    t1 = _make_track(db_session, path="/g1", title="T1", artist="X", album="Al1", album_artist="X")
    t2 = _make_track(db_session, path="/g2", title="T2", artist="Y", album="Al2", album_artist="Y")
    db_session.commit()
    grouping_service.run_cascade(db_session)
    db_session.refresh(t1)
    db_session.refresh(t2)
    group1_id, group2_id = t1.group_id, t2.group_id
    assert group1_id != group2_id

    grouping_service.merge_groups(db_session, into_group_id=group1_id, from_group_ids=[group2_id])

    detail1 = grouping_service.get_group(db_session, group1_id)
    detail2 = grouping_service.get_group(db_session, group2_id)
    assert detail1 is not None
    assert detail2 is not None
    assert detail1.track_count == 2
    assert detail2.track_count == 0


def test_merge_into_self_raises(db_session: Session) -> None:
    with pytest.raises(ValueError, match="cannot merge"):
        grouping_service.merge_groups(db_session, into_group_id=1, from_group_ids=[1])


def test_list_groups_excludes_emptied_groups(db_session: Session) -> None:
    """A merge empties the source TrackGroup row but never deletes it
    (changes/applier.py only moves Track.group_id pointers) — list_groups
    must not surface that now-track-less row as something to review."""
    t1 = _make_track(db_session, path="/j1", title="T1", artist="X", album="Al1", album_artist="X")
    t2 = _make_track(db_session, path="/j2", title="T2", artist="Y", album="Al2", album_artist="Y")
    db_session.commit()
    grouping_service.run_cascade(db_session)
    db_session.refresh(t1)
    db_session.refresh(t2)
    group1_id, group2_id = t1.group_id, t2.group_id
    assert group1_id != group2_id

    grouping_service.merge_groups(db_session, into_group_id=group1_id, from_group_ids=[group2_id])

    listed_ids = {g.id for g in grouping_service.list_groups(db_session)}
    assert group1_id in listed_ids
    assert group2_id not in listed_ids


def test_split_group_applies_immediately(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/e1", title="T1", artist="X", album="Al", album_artist="X")
    t2 = _make_track(db_session, path="/e2", title="T2", artist="X", album="Al", album_artist="X")
    db_session.commit()
    grouping_service.run_cascade(db_session)
    db_session.refresh(t1)
    db_session.refresh(t2)
    original_group_id = t1.group_id
    assert original_group_id is not None
    assert t2.group_id == original_group_id

    cs = grouping_service.split_group(db_session, group_id=original_group_id, track_ids=[t1.id])
    assert cs.state == "applied"

    db_session.refresh(t1)
    db_session.refresh(t2)
    assert t1.group_id != original_group_id
    assert t2.group_id == original_group_id


def test_split_group_no_tracks_raises(db_session: Session) -> None:
    with pytest.raises(ValueError, match="no tracks selected"):
        grouping_service.split_group(db_session, group_id=1, track_ids=[])


def test_split_group_updates_track_count(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/i1", title="T1", artist="X", album="Al", album_artist="X")
    t2 = _make_track(db_session, path="/i2", title="T2", artist="X", album="Al", album_artist="X")
    db_session.commit()
    grouping_service.run_cascade(db_session)
    db_session.refresh(t1)
    db_session.refresh(t2)
    original_group_id = t1.group_id
    assert original_group_id is not None

    grouping_service.split_group(db_session, group_id=original_group_id, track_ids=[t1.id])

    original = grouping_service.get_group(db_session, original_group_id)
    assert original is not None
    assert original.track_count == 1

    db_session.refresh(t1)
    new_group = grouping_service.get_group(db_session, t1.group_id)  # type: ignore[arg-type]
    assert new_group is not None
    assert new_group.track_count == 1


def test_reassign_track_applies_immediately(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/f1", title="T1", artist="X", album="Al1", album_artist="X")
    t2 = _make_track(db_session, path="/f2", title="T2", artist="Y", album="Al2", album_artist="Y")
    db_session.commit()
    grouping_service.run_cascade(db_session)
    db_session.refresh(t1)
    db_session.refresh(t2)
    group1, group2 = t1.group_id, t2.group_id
    assert group1 != group2

    cs = grouping_service.reassign_track(db_session, track_id=t2.id, to_group_id=group1)
    assert cs.state == "applied"

    db_session.refresh(t2)
    assert t2.group_id == group1


def test_reassign_track_updates_track_count_on_both_sides(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/h1", title="T1", artist="X", album="Al1", album_artist="X")
    t2 = _make_track(db_session, path="/h2", title="T2", artist="Y", album="Al2", album_artist="Y")
    db_session.commit()
    grouping_service.run_cascade(db_session)
    db_session.refresh(t1)
    db_session.refresh(t2)
    group1_id, group2_id = t1.group_id, t2.group_id
    assert group1_id != group2_id

    grouping_service.reassign_track(db_session, track_id=t2.id, to_group_id=group1_id)

    detail1 = grouping_service.get_group(db_session, group1_id)
    detail2 = grouping_service.get_group(db_session, group2_id)
    assert detail1 is not None
    assert detail2 is not None
    assert detail1.track_count == 2
    assert detail2.track_count == 0
