from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Change, ChangeSet, Job, Track


def _wait_for_job(client: TestClient, job_id: int, *, timeout: float = 5.0) -> dict[str, Any]:
    """Polls GET /api/jobs/{id} until it reaches a terminal state — the
    `client` fixture's app runs a real worker pool in the background
    (api/app.py's lifespan), so a freshly enqueued job is picked up
    within one poll_interval_seconds tick."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/jobs/{job_id}")
        body = resp.json()
        if body["state"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not reach a terminal state within {timeout}s")


def _seed(db_path: Path, *, title: str = "Original Title") -> int:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session:
        track = Track(
            path=str(db_path.parent / "seed.mp3"),
            filename="seed.mp3",
            ext="mp3",
            size_bytes=1000,
            mtime_ns=1,
            title=title,
            artist="Some Artist",
            first_seen_at=now,
            last_scanned_at=now,
        )
        session.add(track)
        session.commit()
        session.refresh(track)
        return track.id


def test_patch_track_creates_draft_changeset(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db)

    resp = client.patch(f"/api/tracks/{track_id}", json={"fields": {"title": "New Title"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "draft"
    assert body["source"] == "manual_edit"
    assert len(body["changes"]) == 1
    change = body["changes"][0]
    assert change["diff"]["kind"] == "text"
    assert change["diff"]["new_value"] == "New Title"


def test_patch_track_unknown_field_400(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db)
    resp = client.patch(f"/api/tracks/{track_id}", json={"fields": {"bogus_field": "x"}})
    assert resp.status_code == 400


def test_patch_track_missing_404(client: TestClient) -> None:
    resp = client.patch("/api/tracks/999", json={"fields": {"title": "x"}})
    assert resp.status_code == 404


def test_get_changeset_and_patch_decisions(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db)
    resp = client.patch(f"/api/tracks/{track_id}", json={"fields": {"title": "New Title"}})
    cs_id = resp.json()["id"]
    change_id = resp.json()["changes"][0]["id"]

    resp2 = client.get(f"/api/changesets/{cs_id}")
    assert resp2.status_code == 200

    resp3 = client.patch(
        f"/api/changesets/{cs_id}/changes",
        json={"decisions": [{"change_id": change_id, "decision": "accepted"}]},
    )
    assert resp3.status_code == 200
    assert resp3.json()["changes"][0]["decision"] == "accepted"


def test_apply_and_undo_roundtrip(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db)
    seed_path = migrated_db.parent / "seed.mp3"
    import shutil

    fixtures = Path(__file__).parent.parent / "fixtures" / "audio"
    shutil.copy(fixtures / "silence.mp3", seed_path)

    # re-seed with the real path now that the file exists
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        from muzilla.db.models import Track as T

        t = session.get(T, track_id)
        assert t is not None
        t.path = str(seed_path)
        t.tag_hash = None  # unknown baseline -> probe never conflicts
        session.commit()

    resp = client.patch(f"/api/tracks/{track_id}", json={"fields": {"title": "Patched Title"}})
    cs_id = resp.json()["id"]
    change_id = resp.json()["changes"][0]["id"]

    client.patch(
        f"/api/changesets/{cs_id}/changes",
        json={"decisions": [{"change_id": change_id, "decision": "accepted"}]},
    )

    apply_resp = client.post(f"/api/changesets/{cs_id}/apply")
    assert apply_resp.status_code == 202
    apply_job = _wait_for_job(client, apply_resp.json()["job_id"])
    assert apply_job["state"] == "succeeded"
    assert apply_job["result"]["state"] == "applied"

    undo_resp = client.post(f"/api/changesets/{cs_id}/undo")
    assert undo_resp.status_code == 202
    undo_job = _wait_for_job(client, undo_resp.json()["job_id"])
    assert undo_job["state"] == "succeeded"
    undo_cs_id = undo_job["result"]["undo_change_set_id"]

    undo_cs_resp = client.get(f"/api/changesets/{undo_cs_id}")
    assert undo_cs_resp.status_code == 200
    assert undo_cs_resp.json()["source"] == f"undo_of:{cs_id}"


def test_apply_with_backup_true_copies_original(
    migrated_db: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    """Builds its own TestClient rather than using the shared `client`
    fixture: Config is loaded once at app-lifespan startup, and the
    shared fixture's own monkeypatch.setenv calls (for CACHE_DIR/
    BLOB_DIR) already happen inside fixture setup, before this test's
    body would run — so LIBRARY_ROOT/BACKUP_DIR must be set before
    TestClient(create_app()) is constructed here, not after."""
    from muzilla.api.app import create_app

    track_id = _seed(migrated_db)
    library = tmp_path / "library"
    library.mkdir()
    seed_path = library / "seed.mp3"
    import shutil

    fixtures = Path(__file__).parent.parent / "fixtures" / "audio"
    shutil.copy(fixtures / "silence.mp3", seed_path)
    original_bytes = seed_path.read_bytes()

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        t = session.get(Track, track_id)
        assert t is not None
        t.path = str(seed_path)
        t.tag_hash = None
        t.content_hash = "irrelevant-but-must-be-set"
        session.commit()

    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(library))
    monkeypatch.setenv("MUZILLA_STORAGE__BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")

    with TestClient(create_app()) as client:
        csrf = client.get("/api/auth/status").json()["csrf_token"]
        client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": csrf})
        resp = client.patch(f"/api/tracks/{track_id}", json={"fields": {"title": "Patched Title"}})
        cs_id = resp.json()["id"]
        change_id = resp.json()["changes"][0]["id"]
        client.patch(
            f"/api/changesets/{cs_id}/changes",
            json={"decisions": [{"change_id": change_id, "decision": "accepted"}]},
        )

        apply_resp = client.post(f"/api/changesets/{cs_id}/apply", json={"backup": True})
        assert apply_resp.status_code == 202
        apply_job = _wait_for_job(client, apply_resp.json()["job_id"])
        assert apply_job["state"] == "succeeded"
        assert apply_job["result"]["state"] == "applied"

    backup_path = tmp_path / "backups" / "seed.mp3"
    assert backup_path.exists()
    assert backup_path.read_bytes() == original_bytes


def test_apply_with_no_body_still_works(client: TestClient, migrated_db: Path) -> None:
    """The apply body is optional when configuration supplies the defaults."""
    track_id = _seed(migrated_db)
    resp = client.patch(f"/api/tracks/{track_id}", json={"fields": {"title": "New"}})
    cs_id = resp.json()["id"]
    change_id = resp.json()["changes"][0]["id"]
    client.patch(
        f"/api/changesets/{cs_id}/changes",
        json={"decisions": [{"change_id": change_id, "decision": "accepted"}]},
    )

    apply_resp = client.post(f"/api/changesets/{cs_id}/apply")
    assert apply_resp.status_code == 202


def test_apply_rejects_changeset_with_zero_accepted_operations(
    client: TestClient, migrated_db: Path
) -> None:
    track_id = _seed(migrated_db)
    response = client.patch(f"/api/tracks/{track_id}", json={"fields": {"title": "New"}})
    change_set_id = response.json()["id"]

    apply_response = client.post(f"/api/changesets/{change_set_id}/apply")

    assert apply_response.status_code == 400
    assert "no accepted changes" in apply_response.json()["detail"]


def test_apply_idempotency_key_prevents_double_apply(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db)
    seed_path = migrated_db.parent / "idem.mp3"
    import shutil

    fixtures = Path(__file__).parent.parent / "fixtures" / "audio"
    shutil.copy(fixtures / "silence.mp3", seed_path)

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        from muzilla.db.models import Track as T

        t = session.get(T, track_id)
        assert t is not None
        t.path = str(seed_path)
        t.tag_hash = None
        session.commit()

    resp = client.patch(f"/api/tracks/{track_id}", json={"fields": {"title": "Idempotent Title"}})
    cs_id = resp.json()["id"]
    change_id = resp.json()["changes"][0]["id"]
    client.patch(
        f"/api/changesets/{cs_id}/changes",
        json={"decisions": [{"change_id": change_id, "decision": "accepted"}]},
    )

    headers = {"Idempotency-Key": "test-key-123"}
    first = client.post(f"/api/changesets/{cs_id}/apply", headers=headers)
    assert first.status_code == 202
    second = client.post(f"/api/changesets/{cs_id}/apply", headers=headers)
    assert second.status_code == 202
    assert second.json() == first.json()  # same job_id -- not a second enqueue

    job = _wait_for_job(client, first.json()["job_id"])
    assert job["state"] == "succeeded"
    assert job["result"]["state"] == "applied"


def test_bulk_edit_endpoint_is_removed(client: TestClient, migrated_db: Path) -> None:
    id1 = _seed(migrated_db, title="A")
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session:
        t2 = Track(
            path=str(migrated_db.parent / "seed2.mp3"), filename="seed2.mp3", ext="mp3",
            size_bytes=1, mtime_ns=1, title="B", first_seen_at=now, last_scanned_at=now,
        )
        session.add(t2)
        session.commit()
        session.refresh(t2)
        id2 = t2.id

    resp = client.post(
        "/api/tracks/bulk-edit",
        json={"track_ids": [id1, id2], "fields": [{"field": "album_artist", "new_value": "VA"}]},
    )
    assert resp.status_code == 405


def test_find_replace_preview_and_apply_endpoints(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db)
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        from muzilla.db.models import Track as T

        t = session.get(T, track_id)
        assert t is not None
        t.comment = "Ripped by X2008"
        session.commit()

    preview = client.post(
        "/api/tracks/find-replace/preview",
        json={"track_ids": [track_id], "field": "comment", "find": "X2008", "replace": ""},
    )
    assert preview.status_code == 200
    assert len(preview.json()["rows"]) == 1

    apply_resp = client.post(
        "/api/tracks/find-replace",
        json={"track_ids": [track_id], "field": "comment", "find": "X2008", "replace": ""},
    )
    assert apply_resp.status_code == 200
    assert len(apply_resp.json()["changes"]) == 1


def test_strip_endpoint(client: TestClient, migrated_db: Path) -> None:
    track_id = _seed(migrated_db)
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        from muzilla.db.models import Track as T

        t = session.get(T, track_id)
        assert t is not None
        t.comment = "Ripped by LAME"
        session.commit()

    resp = client.post("/api/tracks/strip", json={"track_ids": [track_id]})
    assert resp.status_code == 200
    assert resp.json()["source"] == "strip_tags"


def test_strip_endpoint_uses_settings_strip_fields_override(client: TestClient, migrated_db: Path) -> None:
    """A strip_fields override saved via PUT
    /api/settings/strip-fields must be what /api/tracks/strip actually
    strips, not just what services/settings.py returns in isolation."""
    track_id = _seed(migrated_db)
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        from muzilla.db.models import Track as T

        t = session.get(T, track_id)
        assert t is not None
        t.comment = "Ripped by LAME"
        t.title = "Should Not Be Stripped By Default"
        session.commit()

    put_resp = client.put("/api/settings/strip-fields", json={"fields": ["title"]})
    assert put_resp.status_code == 200

    resp = client.post("/api/tracks/strip", json={"track_ids": [track_id]})
    assert resp.status_code == 200
    body = resp.json()
    fields_touched = {c["field"] for c in body["changes"]}
    assert fields_touched == {"title"}


def test_list_changesets_empty(client: TestClient) -> None:
    resp = client.get("/api/changesets")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "next_cursor": None, "total": 0}


def test_frozen_review_undo_inverse_is_hidden_and_immutable_to_legacy_api(
    client: TestClient, migrated_db: Path
) -> None:
    track_id = _seed(migrated_db)
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        inverse = ChangeSet(
            title="Frozen review undo inverse",
            source="undo_of:42",
            source_ref={"undo_of_id": "42", "review_undo_run_id": "7"},
            state="draft",
            scope_type="track",
            scope_id=track_id,
            created_by="review_bundle_undo",
            stats={"total": 1, "accepted": 1, "rejected": 0, "pending": 0},
            undo_of_id=None,
        )
        inverse.changes.append(
            Change(
                seq=0,
                entity_type="track",
                entity_id=track_id,
                field="title",
                op="set",
                old_value="Applied title",
                new_value="Original title",
                decision="accepted",
            )
        )
        session.add(inverse)
        session.commit()
        inverse_id = inverse.id
        change_id = inverse.changes[0].id

    listed = client.get("/api/changesets")
    fetched = client.get(f"/api/changesets/{inverse_id}")
    patched = client.patch(
        f"/api/changesets/{inverse_id}/changes",
        json={
            "decisions": [
                {
                    "change_id": change_id,
                    "decision": "rejected",
                    "new_value": "Tampered",
                }
            ]
        },
    )
    applied = client.post(f"/api/changesets/{inverse_id}/apply")
    undone = client.post(f"/api/changesets/{inverse_id}/undo")

    assert listed.status_code == 200
    assert listed.json() == {"items": [], "next_cursor": None, "total": 0}
    assert fetched.status_code == 404
    assert patched.status_code == 404
    assert applied.status_code == 404
    assert undone.status_code == 404
    with factory() as session:
        persisted = session.get(ChangeSet, inverse_id)
        assert persisted is not None
        assert persisted.state == "draft"
        assert persisted.changes[0].decision == "accepted"
        assert persisted.changes[0].new_value == "Original title"
        assert session.query(Job).filter(
            Job.type.in_(("apply_changeset", "undo_changeset"))
        ).count() == 0


def test_fields_endpoint(client: TestClient) -> None:
    resp = client.get("/api/fields")
    assert resp.status_code == 200
    names = {f["name"] for f in resp.json()["items"]}
    assert "title" in names
    assert "bitrate" in names
