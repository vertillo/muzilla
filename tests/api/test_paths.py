from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Track

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _seed(db_path: Path, *, filename: str = "seed.mp3", **overrides: object) -> int:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    dest = db_path.parent / filename
    shutil.copy(FIXTURES / "silence.mp3", dest)
    fields: dict[str, object] = {
        "title": "Some Title",
        "artist": "Some Artist",
        **overrides,
    }
    with factory() as session:
        track = Track(
            path=str(dest),
            filename=filename,
            ext="mp3",
            size_bytes=1000,
            mtime_ns=1,
            first_seen_at=now,
            last_scanned_at=now,
            **fields,
        )
        session.add(track)
        session.commit()
        session.refresh(track)
        return track.id


def test_preview_paths_endpoint(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db, artist="The Artist", title="A Song")

    resp = client.post(
        "/api/paths/preview",
        json={"track_ids": [track_id], "template": "$artist - $title"},
    )
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["track_id"] == track_id
    assert rows[0]["new_path"] == "The Artist - A Song"
    assert rows[0]["errors"] == []
    assert rows[0]["is_collision"] is False


def test_preview_paths_unknown_track_400(client: TestClient, migrated_db: Path) -> None:
    resp = client.post("/api/paths/preview", json={"track_ids": [999], "template": "$title"})
    assert resp.status_code == 400


def test_rename_paths_stages_draft_changeset(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db, artist="The Artist", title="A Song")

    resp = client.post(
        "/api/paths/rename",
        json={"track_ids": [track_id], "template": "$artist - $title"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "draft"
    assert body["source"] == "rename"
    assert len(body["changes"]) == 1
    change = body["changes"][0]
    assert change["field"] == "path"
    assert change["op"] == "move"
    assert change["severity"] == "destructive"


def test_rename_paths_refuses_on_collision(client: TestClient, migrated_db: Path) -> None:
    id1 = _seed(migrated_db, filename="one.mp3", artist="Same", title="Name")
    id2 = _seed(migrated_db, filename="two.mp3", artist="Same", title="Name")

    resp = client.post(
        "/api/paths/rename",
        json={"track_ids": [id1, id2], "template": "$artist - $title"},
    )
    assert resp.status_code == 400
