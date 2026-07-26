from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

import muzilla.cli.commands.imports as imports_cli
from muzilla.cli.main import app
from muzilla.providers.set import ProviderSet

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"

runner = CliRunner()


@pytest.fixture(autouse=True)
def stub_build_provider_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """No provider tokens configured in tests -- an empty ProviderSet
    is enough since these tests never reach the match stage with real
    candidates (a nonexistent/empty library root short-circuits scan,
    fingerprint, and group with nothing to do)."""
    empty_set = ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=())
    monkeypatch.setattr(imports_cli, "build_provider_set", lambda config: empty_set)


def test_import_start_without_wait(
    tmp_path: Path, migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    library = tmp_path / "library"
    library.mkdir()

    result = runner.invoke(app, ["import", "start", str(library)])
    assert result.exit_code == 0, result.output
    assert "import session #" in result.output
    assert "started" in result.output


def test_import_start_with_wait_completes(
    tmp_path: Path, migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    library = tmp_path / "library"
    library.mkdir()
    shutil.copy(FIXTURES / "silence.mp3", library / "silence.mp3")

    result = runner.invoke(app, ["import", "start", str(library), "--wait"])
    assert result.exit_code == 0, result.output
    assert "reviewing" in result.output
    assert "scan" in result.output
    assert "fingerprint" in result.output
    assert "group" in result.output
    assert "match" in result.output


def test_import_show(tmp_path: Path, migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    library = tmp_path / "library"
    library.mkdir()

    start_result = runner.invoke(app, ["import", "start", str(library)])
    session_id = start_result.output.split("#")[1].split()[0]

    result = runner.invoke(app, ["import", "show", session_id])
    assert result.exit_code == 0, result.output
    assert "Import session #" in result.output
    assert "scan" in result.output


def test_import_show_missing_fails(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    result = runner.invoke(app, ["import", "show", "99999"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_import_resume(tmp_path: Path, migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    library = tmp_path / "library"
    library.mkdir()

    start_result = runner.invoke(app, ["import", "start", str(library)])
    session_id = start_result.output.split("#")[1].split()[0]

    result = runner.invoke(app, ["import", "resume", session_id])
    assert result.exit_code == 0, result.output
    assert "resumed" in result.output


def test_import_resume_missing_fails(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    result = runner.invoke(app, ["import", "resume", "99999"])
    assert result.exit_code == 1
    assert "error" in result.output
