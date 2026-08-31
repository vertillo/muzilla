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
            ext=".mp3",  # pipeline/scan.py's real code always includes the dot (Path.suffix)
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
    assert rows[0]["new_path"] == "The Artist - A Song.mp3"
    assert rows[0]["errors"] == []
    assert rows[0]["is_collision"] is False
    # PATH-COLLISION-001: preview must expose every conflicting destination and track
    assert "conflicting_track_ids" in rows[0]
    assert "collision_path" in rows[0]


def test_preview_paths_unknown_track_400(client: TestClient, migrated_db: Path) -> None:
    resp = client.post("/api/paths/preview", json={"track_ids": [999], "template": "$title"})
    assert resp.status_code == 400


def test_rename_paths_stages_draft_changeset(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db, artist="The Artist", title="A Song")

    resp = client.post(
        "/api/paths/rename",
        json={"track_ids": [track_id], "template": "$artist - $title"},
    )
    # legacy ChangeSet staging removed per COMPAT-CHANGESET-001 - endpoint now 405
    assert resp.status_code == 405


def test_rename_paths_refuses_on_collision(client: TestClient, migrated_db: Path) -> None:
    id1 = _seed(migrated_db, filename="one.mp3", artist="Same", title="Name")
    id2 = _seed(migrated_db, filename="two.mp3", artist="Same", title="Name")

    resp = client.post(
        "/api/paths/rename",
        json={"track_ids": [id1, id2], "template": "$artist - $title"},
    )
    # legacy ChangeSet staging removed per COMPAT-CHANGESET-001 - endpoint now 405
    assert resp.status_code == 405


def test_preview_uses_settings_template_override_with_no_explicit_template(
    client: TestClient, migrated_db: Path
) -> None:
    """A template saved via PUT /api/settings/
    templates must take effect on the very next preview/rename call, no
    restart — proves api/routers/paths.py's effective_paths_config()
    wiring, not just services/settings.py in isolation."""
    track_id = _seed(migrated_db, artist="The Artist", title="A Song")  # no group -> singleton

    put_resp = client.put("/api/settings/templates", json={"singleton": "$title -- $artist"})
    assert put_resp.status_code == 200

    resp = client.post("/api/paths/preview", json={"track_ids": [track_id]})
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert rows[0]["new_path"] == "A Song -- The Artist.mp3"
