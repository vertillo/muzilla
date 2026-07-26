from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.services.analyze import analyze_library


def _make_track(**overrides: object) -> Track:
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "path": f"/music/{overrides.get('filename', 'track.mp3')}",
        "filename": "track.mp3",
        "ext": "mp3",
        "size_bytes": 1000,
        "mtime_ns": 1,
        "first_seen_at": now,
        "last_scanned_at": now,
    }
    defaults.update(overrides)
    return Track(**defaults)  # type: ignore[arg-type]


def test_analyze_splits_albums_from_singletons(db_session: Session) -> None:
    db_session.add_all(
        [
            _make_track(
                path="/music/a1.mp3",
                filename="a1.mp3",
                album="Ágætis byrjun",
                album_artist="Sigur Rós",
            ),
            _make_track(
                path="/music/a2.mp3",
                filename="a2.mp3",
                album="Ágætis byrjun",
                album_artist="Sigur Rós",
            ),
            _make_track(path="/music/s1.mp3", filename="s1.mp3"),
        ]
    )
    db_session.commit()

    report = analyze_library(db_session)

    assert report.total_tracks == 3
    assert report.album_track_count == 2
    assert report.singleton_track_count == 1
    assert report.inferred_album_count == 1


def test_analyze_field_completeness(db_session: Session) -> None:
    db_session.add_all(
        [
            _make_track(path="/music/a.mp3", filename="a.mp3", title="A", artist="X"),
            _make_track(path="/music/b.mp3", filename="b.mp3", title="B"),
        ]
    )
    db_session.commit()

    report = analyze_library(db_session)
    by_field = {c.field: c for c in report.field_completeness}

    assert by_field["title"].present == 2
    assert by_field["artist"].present == 1
    assert by_field["artist"].missing == 1


def test_analyze_finds_duplicate_artist_title_pairs(db_session: Session) -> None:
    db_session.add_all(
        [
            _make_track(
                path="/music/a.mp3", filename="a.mp3", artist="Sigur Rós", title="Svefn"
            ),
            _make_track(
                path="/music/b.mp3", filename="b.mp3", artist="sigur rós", title="svefn"
            ),
            _make_track(
                path="/music/c.mp3", filename="c.mp3", artist="Sigur Rós", title="Other"
            ),
        ]
    )
    db_session.commit()

    report = analyze_library(db_session)

    assert len(report.duplicate_groups) == 1
    assert len(report.duplicate_groups[0].track_ids) == 2


def test_analyze_format_and_bitrate_breakdown(db_session: Session) -> None:
    db_session.add_all(
        [
            _make_track(path="/music/a.mp3", filename="a.mp3", format="MP3", bitrate=320000),
            _make_track(path="/music/b.flac", filename="b.flac", format="FLAC", bitrate=1000000),
        ]
    )
    db_session.commit()

    report = analyze_library(db_session)

    formats = {f.format: f.count for f in report.format_breakdown}
    assert formats == {"MP3": 1, "FLAC": 1}
    assert report.avg_bitrate == 660000.0


def test_analyze_counts_errors_and_missing(db_session: Session) -> None:
    db_session.add_all(
        [
            _make_track(path="/music/a.mp3", filename="a.mp3", probe_error="boom"),
            _make_track(
                path="/music/b.mp3",
                filename="b.mp3",
                missing_since=datetime.now(UTC),
            ),
        ]
    )
    db_session.commit()

    report = analyze_library(db_session)

    assert report.total_tracks == 1  # missing track excluded from the live set
    assert report.tracks_with_errors == 1
    assert report.tracks_missing == 1
