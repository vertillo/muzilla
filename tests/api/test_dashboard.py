from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Track, TrackGroup


def test_dashboard_summary_empty_library(client: TestClient) -> None:
    resp = client.get("/api/dashboard/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "total_tracks": 0,
        "tracks_missing": 0,
        "tracks_with_errors": 0,
        "tracks_missing_art": 0,
        "album_count": 0,
        "singleton_count": 0,
        "ungrouped_track_count": 0,
    }


def test_dashboard_summary_reflects_seeded_data(client: TestClient, migrated_db: Path) -> None:
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session:
        group = TrackGroup(key="g1", kind="album")
        session.add(group)
        session.flush()
        session.add(
            Track(
                path="/music/a.mp3",
                filename="a.mp3",
                ext="mp3",
                size_bytes=1000,
                mtime_ns=1,
                first_seen_at=now,
                last_scanned_at=now,
                group_id=group.id,
            )
        )
        session.commit()

    resp = client.get("/api/dashboard/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_tracks"] == 1
    assert body["album_count"] == 1
