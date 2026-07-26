from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.db.repo.tracks import get_track, get_track_by_path, list_tracks


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


def _seed(session: Session, count: int = 3) -> None:
    titles = ["Svefn-g-englar", "Starálfur", "Ný batterí"]
    for i in range(count):
        session.add(
            _make_track(
                path=f"/music/track{i}.mp3",
                filename=f"track{i}.mp3",
                title=titles[i % len(titles)],
                artist="Sigur Rós",
                album="Ágætis byrjun",
            )
        )
    session.commit()


def test_list_tracks_returns_all(db_session: Session) -> None:
    _seed(db_session)
    page = list_tracks(db_session)
    assert page.total == 3
    assert len(page.items) == 3
    assert page.next_cursor is None


def test_list_tracks_paginates_with_cursor(db_session: Session) -> None:
    _seed(db_session)
    first = list_tracks(db_session, limit=2)
    assert len(first.items) == 2
    assert first.next_cursor is not None

    second = list_tracks(db_session, limit=2, cursor=first.next_cursor)
    assert len(second.items) == 1
    assert second.next_cursor is None

    seen = {t.id for t in first.items} | {t.id for t in second.items}
    assert len(seen) == 3


def test_list_tracks_fts_search(db_session: Session) -> None:
    _seed(db_session)
    page = list_tracks(db_session, q="Svefn")
    assert page.total == 1
    assert page.items[0].title == "Svefn-g-englar"


def test_list_tracks_excludes_missing(db_session: Session) -> None:
    _seed(db_session)
    track = list_tracks(db_session).items[0]
    track.missing_since = datetime.now(UTC)
    db_session.commit()

    page = list_tracks(db_session)
    assert page.total == 2


def test_get_track_and_by_path(db_session: Session) -> None:
    _seed(db_session, count=1)
    listed = list_tracks(db_session).items[0]

    assert get_track(db_session, listed.id) is not None
    assert get_track_by_path(db_session, "/music/track0.mp3") is not None
    assert get_track_by_path(db_session, "/music/nope.mp3") is None


def test_json_list_columns_roundtrip(db_session: Session) -> None:
    db_session.add(
        _make_track(
            artists=("Sigur Rós", "Jónsi"),
            genre=("Post-Rock", "Ambient"),
        )
    )
    db_session.commit()
    db_session.expunge_all()

    track = get_track_by_path(db_session, "/music/track.mp3")
    assert track is not None
    assert track.artists == ("Sigur Rós", "Jónsi")
    assert track.genre == ("Post-Rock", "Ambient")
    assert track.mood == ()
    assert track.extra_tags == {}
