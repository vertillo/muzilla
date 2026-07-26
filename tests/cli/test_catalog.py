from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from muzilla.cli.main import app

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"

runner = CliRunner()


def test_scan_and_analyze(tmp_path: Path, migrated_db: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))

    library = tmp_path / "library"
    library.mkdir()
    shutil.copy(FIXTURES / "silence.mp3", library / "silence.mp3")
    shutil.copy(FIXTURES / "silence.flac", library / "silence.flac")

    scan_result = runner.invoke(app, ["scan", str(library)])
    assert scan_result.exit_code == 0, scan_result.output
    assert "scanned 2" in scan_result.output
    assert "added 2" in scan_result.output

    analyze_result = runner.invoke(app, ["analyze"])
    assert analyze_result.exit_code == 0, analyze_result.output
    assert "Total tracks:      2" in analyze_result.output
    assert "Formats:" in analyze_result.output


def test_scan_reports_errors_without_crashing(tmp_path: Path, migrated_db: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))

    library = tmp_path / "library"
    library.mkdir()
    (library / "bad.mp3").write_bytes(b"not audio")

    result = runner.invoke(app, ["scan", str(library)])
    assert result.exit_code == 0, result.output
    assert "errors 1" in result.output
