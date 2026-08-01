from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from muzilla.config.schema import Config, StorageConfig
from muzilla.db.engine import create_db_engine
from muzilla.services.migrate import MigrationRunnerNotFoundError, run_migrations

REPO_ROOT = Path(__file__).parent.parent.parent


def _config(db_path: Path) -> Config:
    return Config(storage=StorageConfig(db_path=db_path))


def test_run_migrations_creates_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "muzilla.db"

    run_migrations(_config(db_path))

    engine = create_db_engine(db_path)
    tables = set(inspect(engine).get_table_names())
    assert "tracks" in tables
    assert "track_groups" in tables
    assert "review_bundles" in tables
    assert "source_snapshots" in tables
    assert "proposal_revisions" in tables
    assert "operations" in tables
    assert "apply_runs" in tables
    assert "operation_attempts" in tables
    assert "task_attempts" in tables
    assert "field" in {column["name"] for column in inspect(engine).get_columns("operations")}


def test_review_foundation_migration_preserves_legacy_changesets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "legacy.db"
    env = {"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0010"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    engine = create_db_engine(db_path)
    now = "2026-08-01 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO change_sets
                (id, title, source, source_ref, state, scope_type, scope_id, created_by,
                 stats, created_at, updated_at)
                VALUES (7, 'Legacy draft', 'manual_edit', '{}', 'draft', 'track', 1,
                        'web', '{}', :now, :now)"""
            ),
            {"now": now},
        )

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0010"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT title FROM change_sets WHERE id = 7")) == "Legacy draft"
        assert connection.scalar(text("SELECT COUNT(*) FROM review_bundles")) == 0


def test_run_migrations_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    config = _config(tmp_path / "muzilla.db")

    run_migrations(config)
    run_migrations(config)  # should not raise or duplicate schema


def test_run_migrations_raises_outside_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(MigrationRunnerNotFoundError):
        run_migrations(_config(tmp_path / "muzilla.db"))


def test_run_migrations_logs_start_and_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """docs/PLAN.md §11d: migration runs are a logged boundary."""
    monkeypatch.chdir(REPO_ROOT)
    with caplog.at_level("INFO", logger="muzilla.services.migrate"):
        run_migrations(_config(tmp_path / "muzilla.db"))

    messages = [r.message for r in caplog.records]
    assert "running migrations" in messages
    assert "migrations complete" in messages
