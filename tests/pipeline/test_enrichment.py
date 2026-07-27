from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import Session

from muzilla.audio.replaygain import TrackReplayGain
from muzilla.db.models import Track, TrackGroup
from muzilla.pipeline.enrichment import (
    groups_needing_replaygain,
    stage_replaygain_for_group,
)


def _make_group(session: Session, *, kind: str = "album") -> TrackGroup:
    group = TrackGroup(key=f"key-{kind}-{id(object())}", kind=kind, album="Test Album")
    session.add(group)
    session.flush()
    return group


def _make_track(session: Session, group: TrackGroup, *, path: str, title: str) -> Track:
    t = Track(
        path=path,
        filename=Path(path).name,
        ext=".flac",
        size_bytes=1000,
        mtime_ns=1,
        title=title,
        group_id=group.id,
    )
    session.add(t)
    session.flush()
    return t


def test_groups_needing_replaygain_returns_group_with_unanalyzed_track(db_session: Session) -> None:
    group = _make_group(db_session)
    _make_track(db_session, group, path="/music/a.flac", title="A")
    db_session.commit()

    groups = groups_needing_replaygain(db_session)

    assert [g.id for g in groups] == [group.id]


def test_groups_needing_replaygain_excludes_fully_analyzed_group(db_session: Session) -> None:
    group = _make_group(db_session)
    track = _make_track(db_session, group, path="/music/a.flac", title="A")
    track.rg_track_gain = -3.0
    db_session.commit()

    assert groups_needing_replaygain(db_session) == []


def test_groups_needing_replaygain_excludes_missing_tracks(db_session: Session) -> None:
    group = _make_group(db_session)
    track = _make_track(db_session, group, path="/music/a.flac", title="A")
    track.missing_since = datetime.now(UTC)
    db_session.commit()

    assert groups_needing_replaygain(db_session) == []


def test_stage_replaygain_for_group_sets_track_and_album_fields(db_session: Session) -> None:
    group = _make_group(db_session)
    a = _make_track(db_session, group, path="/music/a.flac", title="A")
    b = _make_track(db_session, group, path="/music/b.flac", title="B")
    db_session.commit()

    results = {
        Path("/music/a.flac"): TrackReplayGain(
            path=Path("/music/a.flac"), track_gain_db=-3.2, track_peak=0.9,
            album_gain_db=-2.5, album_peak=0.95,
        ),
        Path("/music/b.flac"): TrackReplayGain(
            path=Path("/music/b.flac"), track_gain_db=-4.0, track_peak=0.8,
            album_gain_db=-2.5, album_peak=0.95,
        ),
    }
    with patch("muzilla.pipeline.enrichment.compute_album_replaygain", return_value=results):
        change_set = stage_replaygain_for_group(db_session, group)
    db_session.commit()

    assert change_set is not None
    assert change_set.source == "enrichment"
    assert change_set.state == "draft"
    assert change_set.scope_type == "group"
    assert change_set.scope_id == group.id

    by_entity: dict[int, dict[str, object]] = {}
    for change in change_set.changes:
        by_entity.setdefault(change.entity_id, {})[change.field] = change.new_value
        assert change.decision == "accepted"

    assert by_entity[a.id]["rg_track_gain"] == -3.2
    assert by_entity[a.id]["rg_album_gain"] == -2.5
    assert by_entity[b.id]["rg_track_gain"] == -4.0


def test_stage_replaygain_for_group_skips_when_no_tracks_need_it(db_session: Session) -> None:
    group = _make_group(db_session)
    track = _make_track(db_session, group, path="/music/a.flac", title="A")
    track.rg_track_gain = -3.0
    db_session.commit()

    with patch("muzilla.pipeline.enrichment.compute_album_replaygain") as mock_compute:
        result = stage_replaygain_for_group(db_session, group)

    assert result is None
    mock_compute.assert_not_called()


def test_stage_replaygain_for_group_force_reanalyzes_everything(db_session: Session) -> None:
    group = _make_group(db_session)
    track = _make_track(db_session, group, path="/music/a.flac", title="A")
    track.rg_track_gain = -3.0
    db_session.commit()

    results = {
        Path("/music/a.flac"): TrackReplayGain(
            path=Path("/music/a.flac"), track_gain_db=-5.0, track_peak=0.7
        ),
    }
    with patch("muzilla.pipeline.enrichment.compute_album_replaygain", return_value=results):
        change_set = stage_replaygain_for_group(db_session, group, force=True)
    db_session.commit()

    assert change_set is not None
    assert change_set.changes[0].new_value == -5.0


def test_stage_replaygain_for_group_returns_none_when_rsgain_has_no_results(
    db_session: Session,
) -> None:
    group = _make_group(db_session)
    _make_track(db_session, group, path="/music/a.flac", title="A")
    db_session.commit()

    with patch("muzilla.pipeline.enrichment.compute_album_replaygain", return_value={}):
        result = stage_replaygain_for_group(db_session, group)

    assert result is None
