from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Track


def _seed(db_path: Path) -> int:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session:
        track = Track(
            path="/music/track.mp3",
            filename="track.mp3",
            ext="mp3",
            size_bytes=1000,
            mtime_ns=1,
            title="Svefn-g-englar",
            artist="Sigur Rós",
            first_seen_at=now,
            last_scanned_at=now,
        )
        session.add(track)
        session.commit()
        session.refresh(track)
        return track.id


def test_list_tracks_empty(client: TestClient) -> None:
    resp = client.get("/api/tracks")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"items": [], "next_cursor": None, "total": 0}


def test_list_and_get_track(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db)

    resp = client.get("/api/tracks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Svefn-g-englar"

    resp = client.get(f"/api/tracks/{track_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["artist"] == "Sigur Rós"
    assert detail["id"] == track_id


def test_get_track_not_found(client: TestClient) -> None:
    resp = client.get("/api/tracks/999")
    assert resp.status_code == 404


def test_manual_track_edit_opens_a_review(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db)

    response = client.post(
        f"/api/tracks/{track_id}/review/manual", json={"fields": {"title": "Edited title"}}
    )

    assert response.status_code == 200
    review = response.json()
    assert review["scope_type"] == "track"
    assert review["scope_id"] == track_id
    assert review["current_revision"]["operations"][0]["kind"] == "set_tag"
    assert review["current_revision"]["operations"][0]["proposed_value"] == "Edited title"


def test_rescan_track_enqueues_a_file_only_job(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db)

    response = client.post(f"/api/tracks/{track_id}/rescan")

    assert response.status_code == 202
    job = client.get(f"/api/jobs/{response.json()['job_id']}").json()
    assert job["type"] == "rescan_track"


def test_list_tracks_search(client: TestClient, migrated_db: Path) -> None:
    _seed(migrated_db)

    resp = client.get("/api/tracks", params={"q": "Svefn"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = client.get("/api/tracks", params={"q": "nonexistent"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def _seed_two(db_path: Path) -> None:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session:
        session.add_all(
            [
                Track(
                    path="/music/a.mp3",
                    filename="a.mp3",
                    ext="mp3",
                    size_bytes=1000,
                    mtime_ns=1,
                    title="Svefn-g-englar",
                    artist="Sigur Rós",
                    album="Ágætis byrjun",
                    format="flac",
                    genre=("Post-Rock",),
                    first_seen_at=now,
                    last_scanned_at=now,
                ),
                Track(
                    path="/music/b.mp3",
                    filename="b.mp3",
                    ext="mp3",
                    size_bytes=1000,
                    mtime_ns=1,
                    title="Go Do",
                    artist="Jónsi",
                    album=None,
                    format="mp3",
                    genre=("Electronic",),
                    first_seen_at=now,
                    last_scanned_at=now,
                ),
            ]
        )
        session.commit()


def test_list_tracks_filters_by_artist_and_format(client: TestClient, migrated_db: Path) -> None:
    _seed_two(migrated_db)

    resp = client.get("/api/tracks", params={"artist": "Jónsi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["artist"] == "Jónsi"

    resp = client.get("/api/tracks", params={"format": "flac"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_list_tracks_filters_by_flags(client: TestClient, migrated_db: Path) -> None:
    _seed_two(migrated_db)

    resp = client.get("/api/tracks", params={"flags": "unmatched"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["album"] is None


def test_get_track_facets(client: TestClient, migrated_db: Path) -> None:
    _seed_two(migrated_db)

    resp = client.get("/api/tracks/facets")
    assert resp.status_code == 200
    body = resp.json()
    assert {a["value"] for a in body["artists"]} == {"Sigur Rós", "Jónsi"}
    assert {g["value"]: g["count"] for g in body["genres"]} == {
        "Post-Rock": 1,
        "Electronic": 1,
    }


def test_get_track_facets_scoped_to_search(client: TestClient, migrated_db: Path) -> None:
    _seed_two(migrated_db)

    resp = client.get("/api/tracks/facets", params={"q": "Svefn"})
    assert resp.status_code == 200
    body = resp.json()
    assert {a["value"] for a in body["artists"]} == {"Sigur Rós"}


def test_get_track_facets_empty(client: TestClient) -> None:
    resp = client.get("/api/tracks/facets")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"artists": [], "albums": [], "genres": [], "formats": []}
