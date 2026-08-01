from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from muzilla.services.capabilities import CapabilityStatus, RuntimeCapabilities


def _runtime(*, available: bool, enabled: bool = True) -> RuntimeCapabilities:
    if not enabled:
        state = "disabled"
        detail = "disabled by configuration"
    elif available:
        state = "available"
        detail = "operational"
    else:
        state = "unavailable"
        detail = "rsgain executable could not start"
    return RuntimeCapabilities(
        replaygain=CapabilityStatus(
            name="replaygain",
            state=state,
            enabled=enabled,
            available=available,
            detail=detail,
        )
    )


def test_health_ok(client: TestClient) -> None:
    with patch(
        "muzilla.services.capabilities.get_runtime_capabilities",
        side_effect=AssertionError("liveness must not run capability probes"),
    ):
        resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_capabilities_do_not_claim_replaygain_when_probe_fails(client: TestClient) -> None:
    with patch(
        "muzilla.services.capabilities.get_runtime_capabilities",
        return_value=_runtime(available=False),
    ):
        resp = client.get("/api/capabilities")

    assert resp.status_code == 200
    assert resp.json()["replaygain"] == {
        "name": "replaygain",
        "state": "unavailable",
        "enabled": True,
        "available": False,
        "detail": "rsgain executable could not start",
    }


def test_readiness_is_unavailable_when_required_replaygain_probe_fails(
    client: TestClient,
) -> None:
    with patch(
        "muzilla.services.capabilities.get_runtime_capabilities",
        return_value=_runtime(available=False),
    ):
        resp = client.get("/api/ready")

    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert resp.json()["capabilities"]["replaygain"]["available"] is False


def test_readiness_is_ready_when_replaygain_is_operational(client: TestClient) -> None:
    with patch(
        "muzilla.services.capabilities.get_runtime_capabilities",
        return_value=_runtime(available=True),
    ):
        resp = client.get("/api/ready")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_disabled_replaygain_does_not_make_application_unready(client: TestClient) -> None:
    with patch(
        "muzilla.services.capabilities.get_runtime_capabilities",
        return_value=_runtime(available=False, enabled=False),
    ):
        resp = client.get("/api/ready")

    assert resp.status_code == 200
    assert resp.json()["capabilities"]["replaygain"]["state"] == "disabled"


def test_public_capability_endpoints_reuse_one_bounded_probe(client: TestClient) -> None:
    with patch(
        "muzilla.services.capabilities.get_runtime_capabilities",
        return_value=_runtime(available=True),
    ) as probe:
        capabilities_resp = client.get("/api/capabilities")
        readiness_resp = client.get("/api/ready")

    assert capabilities_resp.status_code == 200
    assert readiness_resp.status_code == 200
    probe.assert_called_once()
