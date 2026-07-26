from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import Track
from muzilla.jobs.handlers.scan import handle_scan
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.set import ProviderSet

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "audio"


def _context() -> WorkerContext:
    return WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=Config(),
    )


async def test_handle_scan_upserts_tracks(db_session: Session, tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    shutil.copy(FIXTURES / "silence.mp3", library / "silence.mp3")

    job = enqueue(db_session, type="scan", payload={"root": str(library)})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_scan(db_session, job, progress, _context())

    assert result["scanned"] == 1
    assert result["added"] == 1
    tracks = list(db_session.scalars(select(Track)))
    assert len(tracks) == 1
