from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Track


def _seed_two(db_path: Path) -> tuple[int, int]:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session:
        t1 = Track(
            path="/music/a.mp3", filename="a.mp3", ext="mp3", size_bytes=1, mtime_ns=1,
            title="T1", artist="Artist", album="Album", album_artist="Artist",
            first_seen_at=now, last_scanned_at=now,
        )
        t2 = Track(
            path="/music/b.mp3", filename="b.mp3", ext="mp3", size_bytes=1, mtime_ns=1,
            title="T2", artist="Artist", album="Album", album_artist="Artist",
            first_seen_at=now, last_scanned_at=now,
        )
        session.add_all([t1, t2])
        session.commit()
        session.refresh(t1)
        session.refresh(t2)
        return t1.id, t2.id


def test_run_cascade_and_list_groups(client: TestClient, migrated_db: Path) -> None:
    _seed_two(migrated_db)

    resp = client.post("/api/groups/cascade")
    assert resp.status_code == 200
    assert resp.json()["groups_created"] >= 1

    resp2 = client.get("/api/groups")
    assert resp2.status_code == 200
    assert len(resp2.json()["items"]) >= 1


def test_get_group_detail(client: TestClient, migrated_db: Path) -> None:
    _seed_two(migrated_db)
    client.post("/api/groups/cascade")
    groups = client.get("/api/groups").json()["items"]
    group_id = groups[0]["id"]

    resp = client.get(f"/api/groups/{group_id}")
    assert resp.status_code == 200
    assert "track_ids" in resp.json()


def test_get_group_not_found(client: TestClient) -> None:
    resp = client.get("/api/groups/999")
    assert resp.status_code == 404


def test_pin_group_endpoint(client: TestClient, migrated_db: Path) -> None:
    _seed_two(migrated_db)
    client.post("/api/groups/cascade")
    group_id = client.get("/api/groups").json()["items"][0]["id"]

    resp = client.post(f"/api/groups/{group_id}/pin")
    assert resp.status_code == 200
    assert resp.json()["source"] == "grouping_correction"


def test_force_singleton_endpoint(client: TestClient, migrated_db: Path) -> None:
    t1_id, _t2_id = _seed_two(migrated_db)
    client.post("/api/groups/cascade")

    resp = client.post("/api/groups/force-singleton", json={"track_id": t1_id})
    assert resp.status_code == 200
    assert resp.json()["source"] == "grouping_correction"
