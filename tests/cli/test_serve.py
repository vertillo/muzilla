"""docs/product-spec.md, step 2.5: proxy-header trust is threaded from a CLI
flag into uvicorn.run(), not hardcoded. Mocks uvicorn.run rather than
actually starting a server — `serve` blocks forever otherwise."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from muzilla.cli import main as cli_main

runner = CliRunner()


@pytest.fixture
def mock_uvicorn_run(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock: MagicMock = MagicMock()
    monkeypatch.setattr(cli_main.uvicorn, "run", mock)
    return mock


def test_serve_defaults_forwarded_allow_ips_to_localhost(
    mock_uvicorn_run: MagicMock, migrated_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    result = runner.invoke(cli_main.app, ["serve"])
    assert result.exit_code == 0, result.output
    mock_uvicorn_run.assert_called_once()
    _, kwargs = mock_uvicorn_run.call_args
    assert kwargs["forwarded_allow_ips"] == "127.0.0.1"
    assert kwargs["proxy_headers"] is True


def test_serve_accepts_a_custom_forwarded_allow_ips(
    mock_uvicorn_run: MagicMock, migrated_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    result = runner.invoke(
        cli_main.app, ["serve", "--forwarded-allow-ips", "10.0.0.1,10.0.0.2"]
    )
    assert result.exit_code == 0, result.output
    _, kwargs = mock_uvicorn_run.call_args
    assert kwargs["forwarded_allow_ips"] == "10.0.0.1,10.0.0.2"
