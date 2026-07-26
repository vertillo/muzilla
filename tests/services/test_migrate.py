from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect

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


def test_run_migrations_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    config = _config(tmp_path / "muzilla.db")

    run_migrations(config)
    run_migrations(config)  # should not raise or duplicate schema


def test_run_migrations_raises_outside_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(MigrationRunnerNotFoundError):
        run_migrations(_config(tmp_path / "muzilla.db"))
