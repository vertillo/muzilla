from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from muzilla.cli.main import app
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Track

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"

runner = CliRunner()


def _scan_one(tmp_path: Path, db_path: Path, monkeypatch) -> int:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(db_path))
    library = tmp_path / "library"
    library.mkdir()
    shutil.copy(FIXTURES / "silence.mp3", library / "silence.mp3")
    result = runner.invoke(app, ["scan", str(library)])
    assert result.exit_code == 0, result.output

    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    with factory() as session:
        track = session.query(Track).one()
        return track.id


def test_path_test_renders_for_track(tmp_path: Path, migrated_db: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    track_id = _scan_one(tmp_path, migrated_db, monkeypatch)

    result = runner.invoke(app, ["path-test", "$artist - $title", "--track", str(track_id)])
    assert result.exit_code == 0, result.output
    assert "Sigur Rós - Ágætis byrjun" in result.output


def test_path_test_requires_exactly_one_target(tmp_path: Path, migrated_db: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _scan_one(tmp_path, migrated_db, monkeypatch)

    result = runner.invoke(app, ["path-test", "$title"])
    assert result.exit_code == 1
    assert "exactly one" in result.output


def test_path_test_unknown_track_errors(tmp_path: Path, migrated_db: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _scan_one(tmp_path, migrated_db, monkeypatch)

    result = runner.invoke(app, ["path-test", "$title", "--track", "999999"])
    assert result.exit_code == 1
    assert "error" in result.output
