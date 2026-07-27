from __future__ import annotations

from fastapi.testclient import TestClient


def test_post_enrich_replaygain_enqueues_job(client: TestClient) -> None:
    resp = client.post("/api/enrich/replaygain")
    assert resp.status_code == 202
    assert "job_id" in resp.json()
