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


def test_pin_group_endpoint_applies_immediately(client: TestClient, migrated_db: Path) -> None:
    """docs/KNOWN_BUGS.md #3's fix, exercised through the real HTTP
    surface: clicking Pin used to change nothing observable in
    GET /api/groups because the changeset was never applied. Now the
    response's own `state` is "applied", and a follow-up GET on the
    group confirms is_pinned actually flipped."""
    _seed_two(migrated_db)
    client.post("/api/groups/cascade")
    group_id = client.get("/api/groups").json()["items"][0]["id"]

    resp = client.post(f"/api/groups/{group_id}/pin")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "grouping_correction"
    assert body["state"] == "applied"

    detail = client.get(f"/api/groups/{group_id}").json()
    assert detail["is_pinned"] is True


def test_force_singleton_endpoint_applies_immediately(client: TestClient, migrated_db: Path) -> None:
    t1_id, _t2_id = _seed_two(migrated_db)
    client.post("/api/groups/cascade")
    original_group_id = client.get(f"/api/tracks/{t1_id}").json()["group_id"]

    resp = client.post("/api/groups/force-singleton", json={"track_id": t1_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "grouping_correction"
    assert body["state"] == "applied"

    new_group_id = client.get(f"/api/tracks/{t1_id}").json()["group_id"]
    assert new_group_id != original_group_id


def test_merge_groups_endpoint_applies_immediately(client: TestClient, migrated_db: Path) -> None:
    t1_id, t2_id = _seed_two(migrated_db)
    client.post("/api/groups/cascade")
    g1 = client.get(f"/api/tracks/{t1_id}").json()["group_id"]
    g2 = client.get(f"/api/tracks/{t2_id}").json()["group_id"]
    # cascade groups by (album, album_artist) among other signals — the
    # fixture shares both, so force distinct starting groups via pin +
    # force-singleton first so there's a real merge to prove.
    if g1 == g2:
        client.post("/api/groups/force-singleton", json={"track_id": t2_id})
        g2 = client.get(f"/api/tracks/{t2_id}").json()["group_id"]
    assert g1 != g2

    resp = client.post(f"/api/groups/{g1}/merge", json={"from_group_ids": [g2]})
    assert resp.status_code == 200
    assert resp.json()["state"] == "applied"

    merged_group_id = client.get(f"/api/tracks/{t2_id}").json()["group_id"]
    assert merged_group_id == g1


def test_split_group_endpoint_applies_immediately(client: TestClient, migrated_db: Path) -> None:
    t1_id, t2_id = _seed_two(migrated_db)
    client.post("/api/groups/cascade")
    group_id = client.get(f"/api/tracks/{t1_id}").json()["group_id"]
    assert client.get(f"/api/tracks/{t2_id}").json()["group_id"] == group_id

    resp = client.post(f"/api/groups/{group_id}/split", json={"track_ids": [t1_id]})
    assert resp.status_code == 200
    assert resp.json()["state"] == "applied"

    new_group_id = client.get(f"/api/tracks/{t1_id}").json()["group_id"]
    assert new_group_id != group_id
    assert client.get(f"/api/tracks/{t2_id}").json()["group_id"] == group_id


def test_reassign_track_endpoint_applies_immediately(client: TestClient, migrated_db: Path) -> None:
    t1_id, t2_id = _seed_two(migrated_db)
    client.post("/api/groups/cascade")
    g1 = client.get(f"/api/tracks/{t1_id}").json()["group_id"]

    # Split t2 into its own group first so there's a real reassignment
    # to prove rather than a same-group no-op.
    client.post("/api/groups/force-singleton", json={"track_id": t2_id})

    resp = client.post(f"/api/groups/{g1}/reassign", json={"track_id": t2_id, "to_group_id": g1})
    assert resp.status_code == 200
    assert resp.json()["state"] == "applied"

    assert client.get(f"/api/tracks/{t2_id}").json()["group_id"] == g1
