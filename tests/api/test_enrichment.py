from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.jobs import queue
from muzilla.services.capabilities import CapabilityStatus, RuntimeCapabilities


def _runtime(*, available: bool) -> RuntimeCapabilities:
    return RuntimeCapabilities(
        replaygain=CapabilityStatus(
            name="replaygain",
            state="available" if available else "unavailable",
            enabled=True,
            available=available,
            detail="operational" if available else "rsgain executable could not start",
        ),
        fingerprint=CapabilityStatus(
            name="fingerprint",
            state="available",
            enabled=True,
            available=True,
            detail="operational",
        ),
    )


def test_post_enrich_replaygain_enqueues_job(client: TestClient) -> None:
    with patch(
        "muzilla.services.capabilities.get_runtime_capabilities",
        return_value=_runtime(available=True),
    ):
        resp = client.post("/api/enrich/replaygain")
    assert resp.status_code == 202
    assert "job_id" in resp.json()


def test_post_enrich_replaygain_rejects_unavailable_capability(client: TestClient) -> None:
    with (
        patch(
            "muzilla.services.capabilities.get_runtime_capabilities",
            return_value=_runtime(available=False),
        ),
        patch("muzilla.services.jobs.enqueue_replaygain") as enqueue,
    ):
        resp = client.post("/api/enrich/replaygain")

    assert resp.status_code == 503
    assert resp.json() == {"detail": "ReplayGain unavailable: rsgain executable could not start"}
    enqueue.assert_not_called()


def test_post_enrich_art_enqueues_job(client: TestClient) -> None:
    resp = client.post("/api/enrich/art")
    assert resp.status_code == 202
    assert "job_id" in resp.json()


def test_post_enrich_lyrics_enqueues_job(client: TestClient) -> None:
    resp = client.post("/api/enrich/lyrics")
    assert resp.status_code == 202
    assert "job_id" in resp.json()


def test_retry_failed_lyrics_enqueues_only_transient_item_ids(
    client: TestClient, migrated_db: Path
) -> None:
    factory = create_session_factory(create_db_engine(migrated_db))
    with factory() as session:
        previous = queue.enqueue(session, type="enrich_lyrics", payload={})
        queue.mark_succeeded(
            session,
            previous.id,
            {
                "items": [
                    {"track_id": 10, "outcome": "not_found", "retryable": False},
                    {"track_id": 11, "outcome": "transient_error", "retryable": True},
                    {"track_id": 12, "outcome": "permanent_error", "retryable": False},
                ],
                "retryable_track_ids": [11],
            },
        )

    resp = client.post(f"/api/enrich/lyrics/{previous.id}/retry-failed")

    assert resp.status_code == 202
    retry_id = resp.json()["job_id"]
    with factory() as session:
        retry = queue.get_job(session, retry_id)
        assert retry is not None
        assert retry.payload == {"track_ids": [11], "retry_of": previous.id}
