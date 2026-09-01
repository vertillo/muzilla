from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Track
from muzilla.db.repo.tracks import encode_facet_cursor


def _seed_many(db_path: Path, count: int = 600) -> None:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session:
        for i in range(count):
            session.add(
                Track(
                    path=f"/music/artist_{i:04d}/track.mp3",
                    filename="track.mp3",
                    ext="mp3",
                    size_bytes=1000,
                    mtime_ns=i,
                    title=f"Track {i}",
                    artist=f"Artist {i:04d}",
                    album=f"Album {i % 50:02d}",
                    format="flac" if i % 2 == 0 else "mp3",
                    genre=("Rock",) if i % 3 == 0 else ("Electronic",),
                    first_seen_at=now,
                    last_scanned_at=now,
                )
            )
        session.commit()


def test_facets_paginate_large_cardinality(client: TestClient, migrated_db: Path) -> None:
    _seed_many(migrated_db, 600)

    # Default limit 100 — first page
    resp = client.get("/api/tracks/facets", params={"limit": 100})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["artists"]) == 100
    assert body["artists"][0]["value"] == "Artist 0000"
    assert body["artists"][0]["count"] == 1

    # Second page via cursor (value-cursor/keyset)
    cursor = encode_facet_cursor(body["artists"][-1]["value"])
    resp2 = client.get("/api/tracks/facets", params={"limit": 100, "cursor": cursor})
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert len(body2["artists"]) == 100
    assert body2["artists"][0]["value"] == "Artist 0100"
    # No overlap
    assert {a["value"] for a in body["artists"]}.isdisjoint({a["value"] for a in body2["artists"]})

    # Large limit capped at 500
    resp3 = client.get("/api/tracks/facets", params={"limit": 1000})
    assert resp3.status_code in (200, 422)  # 422 if validation rejects >500, else capped
    if resp3.status_code == 200:
        assert len(resp3.json()["artists"]) <= 500


def test_facets_search_filters_values_case_insensitive(
    client: TestClient, migrated_db: Path
) -> None:
    _seed_many(migrated_db, 100)

    resp = client.get("/api/tracks/facets", params={"facet_q": "artist 000"})
    assert resp.status_code == 200
    body = resp.json()
    # Should match Artist 0000-0009 etc
    assert len(body["artists"]) > 0
    assert all("artist 000" in a["value"].lower() for a in body["artists"])

    # Case insensitive
    resp2 = client.get("/api/tracks/facets", params={"facet_q": "ARTIST 000"})
    assert resp2.json() == body

    # No results shows empty with suggestion handling (frontend)
    resp3 = client.get("/api/tracks/facets", params={"facet_q": "nonexistent-zzzzz"})
    assert resp3.json()["artists"] == []


def test_facets_counts_consistent_with_server_filtering(
    client: TestClient, migrated_db: Path
) -> None:
    _seed_many(migrated_db, 50)

    # Filter by album — artist facets should reflect only tracks with that album,
    # except artist facet excludes its own filter (so picking artist doesn't collapse)
    resp_all = client.get("/api/tracks/facets")
    assert len(resp_all.json()["artists"]) == 50

    # Add artist filter — album facets should narrow to that artist's albums
    resp_filtered = client.get("/api/tracks/facets", params={"artist": "Artist 0001"})
    # Artist facet itself should still show all artists (self-excluded) with counts
    assert len(resp_filtered.json()["artists"]) == 50
    # Album facet should be narrowed
    albums_for_artist = resp_filtered.json()["albums"]
    assert len(albums_for_artist) == 1
    assert albums_for_artist[0]["value"] == "Album 01"

    # Tracks endpoint should be consistent
    tracks_resp = client.get("/api/tracks", params={"artist": "Artist 0001"})
    assert tracks_resp.json()["total"] == 1
    assert tracks_resp.json()["items"][0]["artist"] == "Artist 0001"
    assert albums_for_artist[0]["count"] == tracks_resp.json()["total"]


def test_facets_format_and_genre_with_search_and_pagination(
    client: TestClient, migrated_db: Path
) -> None:
    _seed_many(migrated_db, 200)

    # cursor pagination with facet_q
    resp_first = client.get("/api/tracks/facets", params={"facet_q": "alb", "limit": 5})
    assert resp_first.status_code == 200
    for a in resp_first.json()["albums"]:
        assert "alb" in a["value"].lower()
    # Second page via cursor
    cursor = encode_facet_cursor(resp_first.json()["albums"][-1]["value"])
    resp_second = client.get(
        "/api/tracks/facets", params={"facet_q": "alb", "limit": 5, "cursor": cursor}
    )
    assert resp_first.json()["albums"] != resp_second.json()["albums"]
