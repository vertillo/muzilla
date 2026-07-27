from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from muzilla.api.app import create_app
from muzilla.db.engine import create_db_engine, create_session_factory

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def client(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Auth disabled by default so catalog/track tests don't need to log
    in — see tests/api/test_auth.py for auth-specific coverage.

    MUZILLA_STORAGE__CACHE_DIR must point somewhere writable: the app
    lifespan builds a provider set (services/providers.build_provider_set)
    on every startup, which creates an on-disk HTTP cache directory per
    provider — the packaged default of /data is only valid inside the
    Docker image, not a local test run. MUZILLA_STORAGE__BLOB_DIR is the
    same story for changes/blobstore.py (art thumbnails, Phase 6).
    """
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    """A SQLite file with all Alembic migrations applied, including the
    FTS5 virtual table + triggers — needed by anything that exercises
    search, since Base.metadata.create_all() doesn't know about FTS5."""
    db_path = tmp_path / "test.db"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env={"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
    )
    return db_path


@pytest.fixture
def db_session(migrated_db: Path) -> Iterator[Session]:
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        yield session
