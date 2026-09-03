from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def test_browse_lists_directory(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from muzilla.api.app import create_app

    library = tmp_path / "library"
    library.mkdir()
    (library / "a.mp3").write_bytes((FIXTURES / "silence.mp3").read_bytes())
    (library / "subdir").mkdir()
    (library / "subdir" / "b.flac").write_bytes((FIXTURES / "silence.mp3").read_bytes())
    (library / "cover.jpg").write_bytes(b"\xff\xd8\xff")
    (library / ".git").mkdir()

    # Need app with this library

    # Reuse existing client fixture's library? Simpler: use client's configured library by copying there
    # Instead test browse on tmp library via fresh app
    db_path = tmp_path / "test.db"
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parent.parent.parent,
        env={"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(db_path))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("MUZILLA_STORAGE__PROVIDER_SECRETS_DIR", str(tmp_path / "secrets" / "providers"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(library))

    with TestClient(create_app()) as c:
        csrf = c.get("/api/auth/status").json()["csrf_token"]
        c.headers.update({"Origin": "http://testserver", "X-CSRF-Token": csrf})
        resp = c.get("/api/imports/browse", params={"path": str(library)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == str(library.resolve())
        names = {e["name"] for e in body["entries"]}
        assert "a.mp3" in names
        assert "subdir" in names
        # cover.jpg is ignored sidecar, still listed but marked ignored
        assert any(e["name"] == "cover.jpg" and e["ignored"] for e in body["entries"])
        # .git is excluded dir, listed as blocked/ignored
        assert any(e["name"] == ".git" and e["blocked"] for e in body["entries"])

        # Browse subdir
        resp2 = c.get("/api/imports/browse", params={"path": str(library / "subdir")})
        assert resp2.status_code == 200
        assert any(e["name"] == "b.flac" for e in resp2.json()["entries"])

        # Containment violation: outside root
        resp3 = c.get("/api/imports/browse", params={"path": "/etc"})
        assert resp3.status_code == 400

        # Symlink outside: create symlink inside pointing outside
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.mp3").write_bytes((FIXTURES / "silence.mp3").read_bytes())
        symlink_path = library / "link_out"
        try:
            os.symlink(str(outside), str(symlink_path))
        except OSError:
            pytest.skip("symlink not supported")
        resp4 = c.get("/api/imports/browse", params={"path": str(library)})
        assert resp4.status_code == 200
        assert any(e["name"] == "link_out" and e["is_symlink"] and e["blocked"] for e in resp4.json()["entries"])

        # Preview for subdir
        resp5 = c.post("/api/imports/preview", json={"path": str(library / "subdir")})
        assert resp5.status_code == 200
        assert resp5.json()["supported_count"] == 1
        assert resp5.json()["scope_kind"] == "directory"

        # Preview for file
        resp6 = c.post("/api/imports/preview", json={"path": str(library / "a.mp3")})
        assert resp6.status_code == 200
        assert resp6.json()["supported_count"] == 1
        assert resp6.json()["scope_kind"] == "file"

        # Preview containment violation
        resp7 = c.post("/api/imports/preview", json={"path": "/etc"})
        assert resp7.status_code == 400

        # Preview symlink outside via path itself
        resp8 = c.post("/api/imports/preview", json={"path": str(symlink_path)})
        # symlink resolves outside, so containment should fail
        assert resp8.status_code == 400

        # Start import with file scope
        resp9 = c.post("/api/imports", json={"library_root": str(library / "a.mp3")})
        assert resp9.status_code == 202
        assert resp9.json()["library_root"] == str((library / "a.mp3").resolve())

        # Start import outside should 400
        resp10 = c.post("/api/imports", json={"library_root": "/etc"})
        assert resp10.status_code == 400
        # Start import with symlink outside 400
        resp11 = c.post("/api/imports", json={"library_root": str(symlink_path)})
        assert resp11.status_code == 400

        # Preview dry-run does not write DB: ensure no track created before scan
        from sqlalchemy import select

        from muzilla.db.engine import create_db_engine, create_session_factory

        # we already have db_path, check tracks empty (preview didn't create)
        engine = create_db_engine(db_path)
        factory = create_session_factory(engine)
        with factory() as s:
            from muzilla.db.models import Track

            count = len(list(s.scalars(select(Track))))
            assert count == 0

        # Browse file path should 400
        resp12 = c.get("/api/imports/browse", params={"path": str(library / "a.mp3")})
        assert resp12.status_code == 400
