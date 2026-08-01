from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from muzilla.services.capabilities import CapabilityStatus, RuntimeCapabilities


def _runtime(*, available: bool) -> RuntimeCapabilities:
    return RuntimeCapabilities(
        replaygain=CapabilityStatus(
            name="replaygain",
            state="available" if available else "unavailable",
            enabled=True,
            available=available,
            detail="operational" if available else "rsgain executable could not start",
        )
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
