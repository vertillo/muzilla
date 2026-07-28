from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.services.catalog import browse_tracks, get_track_detail, get_track_facets


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


def test_browse_tracks_returns_summaries(db_session: Session) -> None:
    db_session.add(_make_track(title="Svefn-g-englar", artist="Sigur Rós"))
    db_session.commit()

    page = browse_tracks(db_session)

    assert page.total == 1
    assert len(page.items) == 1
    item = page.items[0]
    assert item.title == "Svefn-g-englar"
    assert item.artist == "Sigur Rós"


def test_get_track_detail_returns_full_fields(db_session: Session) -> None:
    track = _make_track(title="Starálfur", isrc="ISABC1234567")
    db_session.add(track)
    db_session.commit()

    detail = get_track_detail(db_session, track.id)

    assert detail is not None
    assert detail.title == "Starálfur"
    assert detail.isrc == "ISABC1234567"


def test_get_track_detail_missing_returns_none(db_session: Session) -> None:
    assert get_track_detail(db_session, 999) is None


def test_browse_tracks_applies_filters(db_session: Session) -> None:
    db_session.add_all(
        [
            _make_track(path="/music/a.mp3", filename="a.mp3", artist="Sigur Rós", format="flac"),
            _make_track(path="/music/b.mp3", filename="b.mp3", artist="Jónsi", format="mp3"),
        ]
    )
    db_session.commit()

    page = browse_tracks(db_session, artist="Jónsi")
    assert page.total == 1
    assert page.items[0].artist == "Jónsi"

    page = browse_tracks(db_session, format="flac")
    assert page.total == 1
    assert page.items[0].format == "flac"


def test_get_track_facets_returns_distinct_values(db_session: Session) -> None:
    db_session.add_all(
        [
            _make_track(
                path="/music/a.mp3",
                filename="a.mp3",
                artist="Sigur Rós",
                album="Ágætis byrjun",
                genre=("Post-Rock",),
            ),
            _make_track(
                path="/music/b.mp3",
                filename="b.mp3",
                artist="Jónsi",
                album="Go",
                genre=("Post-Rock", "Electronic"),
            ),
        ]
    )
    db_session.commit()

    facets = get_track_facets(db_session)

    assert {f.value for f in facets.artists} == {"Sigur Rós", "Jónsi"}
    assert {f.value for f in facets.albums} == {"Ágætis byrjun", "Go"}
    assert {f.value: f.count for f in facets.genres} == {"Post-Rock": 2, "Electronic": 1}


def test_get_track_facets_scoped_to_search(db_session: Session) -> None:
    db_session.add_all(
        [
            _make_track(path="/music/a.mp3", filename="a.mp3", artist="Sigur Rós", title="Svefn-g-englar"),
            _make_track(path="/music/b.mp3", filename="b.mp3", artist="Jónsi", title="Go Do"),
        ]
    )
    db_session.commit()

    facets = get_track_facets(db_session, q="Svefn")
    assert {f.value for f in facets.artists} == {"Sigur Rós"}
