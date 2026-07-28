"""Security headers (docs/PLAN.md §12c, step 2.3)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _assert_security_headers(headers: dict[str, str]) -> None:
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY"
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "style-src" not in csp  # no inlined <style> in the built SPA shell


def test_api_response_has_security_headers(client: TestClient) -> None:
    resp = client.get("/api/health")
    _assert_security_headers(resp.headers)


def test_spa_shell_has_security_headers(client: TestClient) -> None:
    resp = client.get("/")
    _assert_security_headers(resp.headers)


def test_no_hsts_header(client: TestClient) -> None:
    # TLS is terminated by the tunnel, not this app — see middleware.py's
    # module docstring for why emitting HSTS here would be actively
    # harmful to the plain-HTTP LAN path.
    resp = client.get("/api/health")
    assert "Strict-Transport-Security" not in resp.headers
