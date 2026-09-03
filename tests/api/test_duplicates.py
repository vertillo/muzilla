from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import DuplicateGroup, DuplicateMember, Track


def _seed_duplicate_group(db_path: Path) -> int:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    with factory() as session:
        t1 = Track(path="/a-128.mp3", filename="a-128.mp3", ext=".mp3", size_bytes=1000, mtime_ns=1)
        t2 = Track(path="/a-320.mp3", filename="a-320.mp3", ext=".mp3", size_bytes=2000, mtime_ns=1)
        session.add_all([t1, t2])
        session.flush()
        group = DuplicateGroup(mb_recording_id="rec-1", basis="acoustid")
        session.add(group)
        session.flush()
        session.add(DuplicateMember(group_id=group.id, track_id=t1.id))
        session.add(DuplicateMember(group_id=group.id, track_id=t2.id))
        session.commit()
        return group.id


def test_list_duplicates(client: TestClient, migrated_db: Path) -> None:
    _seed_duplicate_group(migrated_db)

    resp = client.get("/api/duplicates")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["mb_recording_id"] == "rec-1"
    assert len(items[0]["tracks"]) == 2


def test_list_duplicates_excludes_dismissed_by_default(client: TestClient, migrated_db: Path) -> None:
    group_id = _seed_duplicate_group(migrated_db)

    resp = client.post(f"/api/duplicates/{group_id}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["dismissed"] is True

    resp = client.get("/api/duplicates")
    assert resp.json()["items"] == []

    resp = client.get("/api/duplicates?include_dismissed=true")
    assert len(resp.json()["items"]) == 1


def test_dismiss_missing_group_404(client: TestClient) -> None:
    resp = client.post("/api/duplicates/99999/dismiss")
    assert resp.status_code == 404


def test_post_duplicates_detect_enqueues_job(client: TestClient) -> None:
    resp = client.post("/api/duplicates/detect")
    assert resp.status_code == 202
    assert "job_id" in resp.json()


def test_list_duplicates_includes_evidence_serialization(client: TestClient, migrated_db: Path) -> None:
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        from datetime import UTC, datetime

        t1 = Track(path="/b-128.mp3", filename="b-128.mp3", ext=".mp3", size_bytes=1000, mtime_ns=1, duration_ms=200000, bitrate=128, format="MP3", first_seen_at=datetime.now(UTC), last_scanned_at=datetime.now(UTC))
        t2 = Track(path="/b-320.mp3", filename="b-320.mp3", ext=".mp3", size_bytes=2000, mtime_ns=1, duration_ms=202000, bitrate=320, format="MP3", first_seen_at=datetime.now(UTC), last_scanned_at=datetime.now(UTC))
        session.add_all([t1, t2])
        session.flush()
        group = DuplicateGroup(mb_recording_id="rec-evidence", basis="acoustid", confidence=0.85, evidence={"confidence": 0.85, "confidence_label": "alta", "confidence_explanation": "test", "duration": {"min_ms": 200000, "max_ms": 202000, "delta_ms": 2000, "delta_percent": 1.0}, "quality": [{"track_id": t1.id, "format": "MP3", "bitrate": 128}], "is_uncertain": False, "is_false_positive_candidate": False, "reason": "test reason", "recording_id": "rec-evidence", "basis": "acoustid"})
        session.add(group)
        session.flush()
        session.add(DuplicateMember(group_id=group.id, track_id=t1.id))
        session.add(DuplicateMember(group_id=group.id, track_id=t2.id))
        session.commit()
    resp = client.get("/api/duplicates")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["confidence"] == 0.85
    assert items[0]["evidence"] is not None
    assert items[0]["evidence"]["confidence"] == 0.85
    assert items[0]["evidence"]["confidence_label"] == "alta"
    assert items[0]["evidence"]["duration"]["delta_percent"] == 1.0
    assert items[0]["evidence"]["quality"][0]["bitrate"] == 128
