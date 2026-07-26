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


def test_list_tracks_search(client: TestClient, migrated_db: Path) -> None:
    _seed(migrated_db)

    resp = client.get("/api/tracks", params={"q": "Svefn"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = client.get("/api/tracks", params={"q": "nonexistent"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
