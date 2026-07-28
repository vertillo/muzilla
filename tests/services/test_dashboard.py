from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from muzilla.db.models import Track, TrackGroup
from muzilla.services.dashboard import get_dashboard_summary


def _make_track(**overrides: object) -> Track:
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "path": "/music/track.mp3",
        "filename": "track.mp3",
        "ext": "mp3",
        "size_bytes": 1000,
        "mtime_ns": 1,
        "first_seen_at": now,
        "last_scanned_at": now,
    }
    defaults.update(overrides)
    return Track(**defaults)  # type: ignore[arg-type]


def test_empty_library_summary_is_all_zero(db_session: Session) -> None:
    summary = get_dashboard_summary(db_session)
    assert summary.total_tracks == 0
    assert summary.tracks_missing == 0
    assert summary.tracks_with_errors == 0
    assert summary.tracks_missing_art == 0
    assert summary.album_count == 0
    assert summary.singleton_count == 0
    assert summary.ungrouped_track_count == 0


def test_counts_tracks_errors_and_missing_art(db_session: Session) -> None:
    db_session.add_all(
        [
            _make_track(path="/music/a.mp3", filename="a.mp3", has_embedded_art=True),
            _make_track(path="/music/b.mp3", filename="b.mp3", has_embedded_art=False),
            _make_track(path="/music/c.mp3", filename="c.mp3", probe_error="bad header"),
        ]
    )
    db_session.commit()

    summary = get_dashboard_summary(db_session)
    assert summary.total_tracks == 3
    assert summary.tracks_with_errors == 1
    assert summary.tracks_missing_art == 2


def test_excludes_missing_tracks_from_total_but_counts_them_separately(db_session: Session) -> None:
    db_session.add_all(
        [
            _make_track(path="/music/a.mp3", filename="a.mp3"),
            _make_track(path="/music/b.mp3", filename="b.mp3", missing_since=datetime.now(UTC)),
        ]
    )
    db_session.commit()

    summary = get_dashboard_summary(db_session)
    assert summary.total_tracks == 1
    assert summary.tracks_missing == 1


def test_album_and_singleton_counts_come_from_track_groups(db_session: Session) -> None:
    db_session.add_all(
        [
            TrackGroup(key="g1", kind="album"),
            TrackGroup(key="g2", kind="partial_album"),
            TrackGroup(key="g3", kind="singleton"),
            TrackGroup(key="g4", kind="singleton"),
            TrackGroup(key="g5", kind="unknown"),
        ]
    )
    db_session.commit()

    summary = get_dashboard_summary(db_session)
    assert summary.album_count == 2  # album + partial_album
    assert summary.singleton_count == 2


def test_ungrouped_track_count(db_session: Session) -> None:
    group = TrackGroup(key="g1", kind="album")
    db_session.add(group)
    db_session.flush()

    db_session.add_all(
        [
            _make_track(path="/music/a.mp3", filename="a.mp3", group_id=group.id),
            _make_track(path="/music/b.mp3", filename="b.mp3", group_id=None),
        ]
    )
    db_session.commit()

    summary = get_dashboard_summary(db_session)
    assert summary.ungrouped_track_count == 1
