"""SPA catch-all path containment coverage.

`GET /{full_path:path}` is unauthenticated by design — it's how a
logged-out client gets the SPA shell in the first place — so containment
has to hold entirely on path resolution, not on require_auth.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muzilla.api import app as app_module

_TRAVERSAL_PATHS = [
    "//etc/passwd",
    "/%2fetc%2fpasswd",
    "/../../../etc/passwd",
    "/..%2f..%2f..%2fetc%2fpasswd",
    "/..%252f..%252fetc%252fpasswd",
]


@pytest.fixture(autouse=True)
def spa_static_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give SPA routing tests deterministic build output.

    The real Vite output is ignored by Git and produced by frontend/Docker
    builds, so API tests use deterministic static-serving fixtures.
    """
    static_dir = tmp_path / "spa-static"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<!doctype html><title>Muzilla</title>")
    (assets_dir / "app-abc123.js").write_text("console.log('muzilla')")
    monkeypatch.setattr(app_module, "_STATIC_DIR", static_dir)
    return static_dir


@pytest.mark.parametrize("path", _TRAVERSAL_PATHS)
def test_traversal_attempts_never_return_foreign_file_content(
    client: TestClient, spa_static_dir: Path, path: str
) -> None:
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        # 200 is only acceptable as the SPA shell itself.
        assert b"root:" not in resp.content
        assert resp.content == (spa_static_dir / "index.html").read_bytes()


def test_traversal_to_configured_db_path_is_contained(
    client: TestClient, migrated_db: Path, spa_static_dir: Path
) -> None:
    # A relative-traversal path built from the real db_path's structure —
    # the containment check must hold regardless of what the attacker
    # targets, not just /etc/passwd.
    rel = os.path.relpath(migrated_db, spa_static_dir)
    resp = client.get(f"/{rel}", follow_redirects=False)
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert resp.content == (spa_static_dir / "index.html").read_bytes()


def test_symlink_inside_static_dir_pointing_outside_is_contained(
    client: TestClient, tmp_path: Path, spa_static_dir: Path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve this")
    link = spa_static_dir / "evil-symlink.txt"
    link.symlink_to(secret)
    try:
        resp = client.get("/evil-symlink.txt", follow_redirects=False)
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert resp.content == (spa_static_dir / "index.html").read_bytes()
        assert resp.content != b"do not serve this"
    finally:
        link.unlink()


def test_legitimate_asset_still_served(client: TestClient, spa_static_dir: Path) -> None:
    resp = client.get("/index.html")
    assert resp.status_code == 200
    assert resp.content == (spa_static_dir / "index.html").read_bytes()


def test_spa_shell_fallback_sends_no_cache(client: TestClient, spa_static_dir: Path) -> None:
    # A stale cached index.html can reference asset
    # hashes that no longer exist after an upgrade. Hit a route that
    # isn't a real file so the fallback branch serves the shell.
    resp = client.get("/dev/components")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


def test_hashed_asset_does_not_get_no_cache(client: TestClient, spa_static_dir: Path) -> None:
    assets_dir = spa_static_dir / "assets"
    asset_name = next(assets_dir.iterdir()).name
    resp = client.get(f"/assets/{asset_name}")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") != "no-cache"
