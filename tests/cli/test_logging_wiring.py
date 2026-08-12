from __future__ import annotations

import logging
from pathlib import Path

import pytest
from typer.testing import CliRunner

from muzilla.cli.main import app

runner = CliRunner()


def test_any_cli_command_configures_logging(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/product-spec.md: configure_logging is called once from the CLI
    entry point (the @app.callback(), which Typer always runs before
    any subcommand) -- proven here by checking the root logger actually
    has a handler with our JSON formatter installed after invoking an
    arbitrary command, rather than just trusting the wiring reads
    correctly. Doesn't assert an exact handler count: pytest's own
    caplog plumbing adds handlers of its own in this environment."""
    from muzilla.logging import _JsonFormatter

    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))

    result = runner.invoke(app, ["jobs", "list"])

    assert result.exit_code == 0, result.output
    root = logging.getLogger()
    assert any(isinstance(h.formatter, _JsonFormatter) for h in root.handlers)
