from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.db.repo.tracks import get_facets, get_track, get_track_by_path, list_tracks


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


def _seed_faceted(session: Session) -> None:
    session.add_all(
        [
            _make_track(
                path="/music/a.mp3",
                filename="a.mp3",
                artist="Sigur Rós",
                album="Ágætis byrjun",
                format="flac",
                genre=("Post-Rock", "Ambient"),
            ),
            _make_track(
                path="/music/b.mp3",
                filename="b.mp3",
                artist="Sigur Rós",
                album="Kveikur",
                format="flac",
                genre=("Post-Rock",),
            ),
            _make_track(
                path="/music/c.mp3",
                filename="c.mp3",
                artist="Jónsi",
                album=None,
                format="mp3",
                genre=("Ambient",),
            ),
        ]
    )
    session.commit()


def test_list_tracks_filters_by_artist(db_session: Session) -> None:
    _seed_faceted(db_session)
    page = list_tracks(db_session, artist="Jónsi")
    assert page.total == 1
    assert page.items[0].path == "/music/c.mp3"


def test_list_tracks_filters_by_album(db_session: Session) -> None:
    _seed_faceted(db_session)
    page = list_tracks(db_session, album="Kveikur")
    assert page.total == 1
    assert page.items[0].path == "/music/b.mp3"


def test_list_tracks_filters_by_format(db_session: Session) -> None:
    _seed_faceted(db_session)
    page = list_tracks(db_session, format="mp3")
    assert page.total == 1
    assert page.items[0].path == "/music/c.mp3"


def test_list_tracks_filters_by_genre_json_array(db_session: Session) -> None:
    _seed_faceted(db_session)
    page = list_tracks(db_session, genre="Ambient")
    assert page.total == 2
    assert {t.path for t in page.items} == {"/music/a.mp3", "/music/c.mp3"}


def test_list_tracks_filters_by_flag_missing_art(db_session: Session) -> None:
    _seed_faceted(db_session)
    page = list_tracks(db_session, flags=("missing-art",))
    assert page.total == 3  # none have embedded art in this fixture


def test_list_tracks_filters_by_flag_unmatched(db_session: Session) -> None:
    _seed_faceted(db_session)
    page = list_tracks(db_session, flags=("unmatched",))
    assert page.total == 1
    assert page.items[0].path == "/music/c.mp3"


def test_list_tracks_filters_by_flag_errored(db_session: Session) -> None:
    _seed_faceted(db_session)
    page = list_tracks(db_session, flags=("errored",))
    assert page.total == 0


def test_list_tracks_combines_multiple_filters(db_session: Session) -> None:
    _seed_faceted(db_session)
    page = list_tracks(db_session, artist="Sigur Rós", genre="Post-Rock")
    assert page.total == 2


def test_get_facets_returns_distinct_values_with_counts(db_session: Session) -> None:
    _seed_faceted(db_session)
    facets = get_facets(db_session)

    assert {f.value: f.count for f in facets.artists} == {"Sigur Rós": 2, "Jónsi": 1}
    assert {f.value for f in facets.albums} == {"Ágætis byrjun", "Kveikur"}
    assert {f.value: f.count for f in facets.formats} == {"flac": 2, "mp3": 1}
    assert {f.value: f.count for f in facets.genres} == {
        "Post-Rock": 2,
        "Ambient": 2,
    }


def test_get_facets_scoped_to_search(db_session: Session) -> None:
    _seed_faceted(db_session)
    facets = get_facets(db_session, q="Kveikur")

    assert {f.value for f in facets.artists} == {"Sigur Rós"}
    assert {f.value for f in facets.albums} == {"Kveikur"}


def test_get_facets_excludes_missing_tracks(db_session: Session) -> None:
    _seed_faceted(db_session)
    track = list_tracks(db_session, artist="Jónsi").items[0]
    track.missing_since = datetime.now(UTC)
    db_session.commit()

    facets = get_facets(db_session)
    assert "Jónsi" not in {f.value for f in facets.artists}


def test_get_facets_empty_library(db_session: Session) -> None:
    facets = get_facets(db_session)
    assert facets.artists == []
    assert facets.albums == []
    assert facets.genres == []
    assert facets.formats == []
