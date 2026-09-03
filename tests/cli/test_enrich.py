from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from muzilla.cli.main import app
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Job
from muzilla.services.capabilities import CapabilityStatus, RuntimeCapabilities

runner = CliRunner()


def _unavailable_runtime() -> RuntimeCapabilities:
    return RuntimeCapabilities(
        replaygain=CapabilityStatus(
            name="replaygain",
            state="unavailable",
            enabled=True,
            available=False,
            detail="rsgain executable could not start",
        ),
        fingerprint=CapabilityStatus(
            name="fingerprint",
            state="available",
            enabled=True,
            available=True,
            detail="operational",
        ),
    )


def test_replaygain_cli_does_not_enqueue_when_capability_is_unavailable(
    migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    with patch(
        "muzilla.services.capabilities.get_runtime_capabilities",
        return_value=_unavailable_runtime(),
    ):
        result = runner.invoke(app, ["enrich", "replaygain"])

    assert result.exit_code == 1
    assert "ReplayGain unavailable: rsgain executable could not start" in result.output

    factory = create_session_factory(create_db_engine(migrated_db))
    with factory() as session:
        assert session.query(Job).filter_by(type="enrich_replaygain").count() == 0
