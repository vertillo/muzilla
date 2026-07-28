"""Regression coverage for the SPA catch-all path-traversal fix
(docs/PLAN.md §12c, step 2.1).

`GET /{full_path:path}` is unauthenticated by design — it's how a
logged-out client gets the SPA shell in the first place — so containment
has to hold entirely on path resolution, not on require_auth.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muzilla.api.app import _STATIC_DIR

_TRAVERSAL_PATHS = [
    "//etc/passwd",
    "/%2fetc%2fpasswd",
    "/../../../etc/passwd",
    "/..%2f..%2f..%2fetc%2fpasswd",
    "/..%252f..%252fetc%252fpasswd",
]


@pytest.mark.parametrize("path", _TRAVERSAL_PATHS)
def test_traversal_attempts_never_return_foreign_file_content(
    client: TestClient, path: str
) -> None:
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        # 200 is only acceptable as the SPA shell itself.
        assert b"root:" not in resp.content
        assert resp.content == (_STATIC_DIR / "index.html").read_bytes()


def test_traversal_to_configured_db_path_is_contained(
    client: TestClient, migrated_db: Path
) -> None:
    # A relative-traversal path built from the real db_path's structure —
    # the containment check must hold regardless of what the attacker
    # targets, not just /etc/passwd.
    rel = os.path.relpath(migrated_db, _STATIC_DIR)
    resp = client.get(f"/{rel}", follow_redirects=False)
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert resp.content == (_STATIC_DIR / "index.html").read_bytes()


def test_symlink_inside_static_dir_pointing_outside_is_contained(
    client: TestClient, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve this")
    link = _STATIC_DIR / "evil-symlink.txt"
    link.symlink_to(secret)
    try:
        resp = client.get("/evil-symlink.txt", follow_redirects=False)
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert resp.content == (_STATIC_DIR / "index.html").read_bytes()
        assert resp.content != b"do not serve this"
    finally:
        link.unlink()


def test_legitimate_asset_still_served(client: TestClient) -> None:
    resp = client.get("/index.html")
    assert resp.status_code == 200
    assert resp.content == (_STATIC_DIR / "index.html").read_bytes()


def test_spa_shell_fallback_sends_no_cache(client: TestClient) -> None:
    # docs/PLAN.md §9: a stale cached index.html can reference asset
    # hashes that no longer exist after an upgrade. Hit a route that
    # isn't a real file so the fallback branch serves the shell.
    resp = client.get("/dev/components")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


def test_hashed_asset_does_not_get_no_cache(client: TestClient) -> None:
    assets_dir = _STATIC_DIR / "assets"
    asset_name = next(assets_dir.iterdir()).name
    resp = client.get(f"/assets/{asset_name}")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") != "no-cache"
