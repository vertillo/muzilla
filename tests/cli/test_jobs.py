from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from muzilla.cli.main import app
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.jobs import queue

runner = CliRunner()


def _enqueue_scan(db_path: Path) -> int:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    with factory() as session:
        job = queue.enqueue(session, type="scan", payload={"root": "/music"})
        return job.id


def test_jobs_list(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    _enqueue_scan(migrated_db)

    result = runner.invoke(app, ["jobs", "list"])
    assert result.exit_code == 0, result.output
    assert "scan" in result.output
    assert "1 job(s)" in result.output


def test_jobs_show(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    job_id = _enqueue_scan(migrated_db)

    result = runner.invoke(app, ["jobs", "show", str(job_id)])
    assert result.exit_code == 0, result.output
    assert f"Job #{job_id}" in result.output
    assert "scan" in result.output


def test_jobs_show_missing_fails(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    result = runner.invoke(app, ["jobs", "show", "99999"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_jobs_cancel(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    job_id = _enqueue_scan(migrated_db)

    result = runner.invoke(app, ["jobs", "cancel", str(job_id)])
    assert result.exit_code == 0, result.output
    assert "cancellation requested" in result.output

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        job = queue.get_job(session, job_id)
        assert job is not None
        assert job.cancel_requested is True


def test_jobs_cancel_missing_fails(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    result = runner.invoke(app, ["jobs", "cancel", "99999"])
    assert result.exit_code == 1
    assert "error" in result.output


def test_jobs_retention(migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))

    result = runner.invoke(app, ["jobs", "retention"])
    assert result.exit_code == 0, result.output
    assert "journals pruned: 0" in result.output
    # legacy ChangeSet wording removed per COMPAT-CHANGESET-001 - now journals/provider cache only
    assert "provider cache rows pruned: 0" in result.output
