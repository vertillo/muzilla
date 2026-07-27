"""Runs Alembic migrations against the configured database.

Invoked as a subprocess (`alembic upgrade head`) rather than through
Alembic's Python API: `migrations/env.py` already resolves its target
DB from `MUZILLA_ALEMBIC_DB_PATH`, the same convention the test suite's
`migrated_db` fixture uses, so this reuses that path instead of
duplicating Alembic's config wiring inside the app.

Requires running with `alembic.ini`/`migrations/` in the current
working directory — true for local dev (repo root) and for the Docker
image (`WORKDIR /app`, which the Dockerfile copies both into), since
neither ships inside the installed `muzilla` wheel itself.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from muzilla.config.schema import Config

_logger = logging.getLogger(__name__)


class MigrationRunnerNotFoundError(Exception):
    pass


def run_migrations(config: Config) -> None:
    if not Path("alembic.ini").is_file():
        raise MigrationRunnerNotFoundError(
            "alembic.ini not found in the current working directory; "
            "run from the muzilla repo root (or /app in the Docker image)."
        )
    config.storage.db_path.parent.mkdir(parents=True, exist_ok=True)
    _logger.info("running migrations", extra={"db_path": str(config.storage.db_path)})
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={
            **os.environ,
            "MUZILLA_ALEMBIC_DB_PATH": str(config.storage.db_path),
        },
        check=True,
        capture_output=True,
    )
    _logger.info("migrations complete", extra={"db_path": str(config.storage.db_path)})
