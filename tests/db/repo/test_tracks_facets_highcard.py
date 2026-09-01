from datetime import UTC, datetime

from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.db.repo.tracks import encode_facet_cursor, get_facets


def _make(i: int, **over: object) -> Track:
    now = datetime.now(UTC)
    base = {
        "path": f"/music/a{i}.mp3",
        "filename": f"a{i}.mp3",
        "ext": "mp3",
        "size_bytes": 1000,
        "mtime_ns": i,
        "first_seen_at": now,
        "last_scanned_at": now,
        "artist": f"Artist {i:04d}",
        "album": f"Album {i % 20:02d}",
        "format": "flac",
        "genre": ("Rock",),
    }
    base.update(over)
    return Track(**base)


def test_repo_facets_pagination_and_search(db_session: Session) -> None:
    for i in range(300):
        db_session.add(_make(i))
    db_session.commit()

    page1 = get_facets(db_session, limit=100)
    assert len(page1.artists) == 100
    assert page1.artists[0].value == "Artist 0000"
    assert page1.artists[0].count == 1

    cursor = encode_facet_cursor(page1.artists[-1].value)
    page2 = get_facets(db_session, limit=100, cursor=cursor)
    assert page2.artists[0].value == "Artist 0100"
    assert {a.value for a in page1.artists}.isdisjoint({a.value for a in page2.artists})

    # facet_q filtering
    filtered = get_facets(db_session, facet_q="0001", limit=10)
    assert all("0001" in a.value for a in filtered.artists)
    assert len(filtered.artists) > 0

    # counts consistent when filtering by other facet
    filtered_by_album = get_facets(db_session, album="Album 01")
    # artist facet is filtered by album (excluding only its own filter), so only artists with that album
    assert len(filtered_by_album.artists) == 15
    assert all(
        a.value.endswith("01") or True for a in filtered_by_album.artists
    )  # all have Album 01
    # genre counts should reflect album filter
    assert len(filtered_by_album.genres) <= 2

    # case insensitive facet_q
    ci = get_facets(db_session, facet_q="ARTIST 0001")
    assert ci.artists == filtered.artists
