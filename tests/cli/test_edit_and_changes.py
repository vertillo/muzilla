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


def test_edit_creates_draft_changeset(tmp_path: Path, migrated_db: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    track_id = _scan_one(tmp_path, migrated_db, monkeypatch)

    result = runner.invoke(app, ["edit", str(track_id), "-f", "title=New Title"])
    assert result.exit_code == 0, result.output
    assert "created changeset" in result.output
    assert "draft" in result.output


def test_edit_invalid_field_syntax(tmp_path: Path, migrated_db: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    track_id = _scan_one(tmp_path, migrated_db, monkeypatch)
    result = runner.invoke(app, ["edit", str(track_id), "-f", "no-equals-sign"])
    assert result.exit_code != 0


def test_edit_unknown_field_fails(tmp_path: Path, migrated_db: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    track_id = _scan_one(tmp_path, migrated_db, monkeypatch)
    result = runner.invoke(app, ["edit", str(track_id), "-f", "bogus=x"])
    assert result.exit_code == 1
    assert "error" in result.output


def _changeset_id_from_edit_output(output: str) -> int:
    for line in output.splitlines():
        if line.startswith("created changeset"):
            return int(line.split()[2])
    raise AssertionError(f"could not find changeset id in: {output}")


def test_changes_show_apply_undo_flow(tmp_path: Path, migrated_db: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    track_id = _scan_one(tmp_path, migrated_db, monkeypatch)

    edit_result = runner.invoke(app, ["edit", str(track_id), "-f", "title=Changed Title"])
    cs_id = _changeset_id_from_edit_output(edit_result.output)

    show_result = runner.invoke(app, ["changes", "show", str(cs_id)])
    assert show_result.exit_code == 0
    assert "ChangeSet" in show_result.output
    assert "title" in show_result.output

    # accept the pending change directly via the service layer, mirroring
    # what the review UI's PATCH would do — the CLI itself has no
    # decide-only command yet (that's the API/web review flow).
    from muzilla.config.loader import load_config
    from muzilla.db.models import ChangeSet
    from muzilla.services.db import session_scope

    config = load_config()
    with session_scope(config) as session:
        cs = session.get(ChangeSet, cs_id)
        assert cs is not None
        for c in cs.changes:
            c.decision = "accepted"
        session.commit()

    apply_result = runner.invoke(app, ["changes", "apply", str(cs_id)])
    assert apply_result.exit_code == 0, apply_result.output
    assert "applied" in apply_result.output

    undo_result = runner.invoke(app, ["changes", "undo", str(cs_id)])
    assert undo_result.exit_code == 0, undo_result.output
    assert "applied" in undo_result.output


def test_changes_list(tmp_path: Path, migrated_db: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    track_id = _scan_one(tmp_path, migrated_db, monkeypatch)
    runner.invoke(app, ["edit", str(track_id), "-f", "title=X"])

    result = runner.invoke(app, ["changes", "list"])
    assert result.exit_code == 0
    assert "1 of 1 changeset" in result.output


def test_changes_apply_unknown_changeset_fails(migrated_db: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    result = runner.invoke(app, ["changes", "apply", "999"])
    assert result.exit_code == 1
    assert "error" in result.output
