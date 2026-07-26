from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from muzilla.db.models import Track, TrackFingerprintMatch
from muzilla.jobs.handlers.fingerprint import handle_fingerprint
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.base import FingerprintMatch
from muzilla.providers.set import ProviderSet

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "audio"

_HAS_FPCALC = shutil.which("fpcalc") is not None
requires_fpcalc = pytest.mark.skipif(
    not _HAS_FPCALC, reason="fpcalc not installed (present in the Docker image, not local dev)"
)


class _StubAcoustID:
    async def lookup(self, fingerprint: str, duration_s: float) -> list[FingerprintMatch]:
        return [
            FingerprintMatch(
                mb_recording_id="rec-1", mb_release_ids=("rel-1", "rel-2"), score=0.9
            )
        ]


def _make_track(session: Session, *, path: str, **kwargs: object) -> Track:
    t = Track(path=path, filename=path.rsplit("/", 1)[-1], ext=".mp3", size_bytes=1000, mtime_ns=1, **kwargs)
    session.add(t)
    session.flush()
    return t


async def test_handle_fingerprint_skips_when_acoustid_not_configured(
    db_session: Session,
) -> None:
    _make_track(db_session, path="/a1", title="T1")
    db_session.commit()

    context = WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=())
    )
    job = enqueue(db_session, type="fingerprint", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_fingerprint(db_session, job, progress, context)

    assert result == {"fingerprinted": 0, "errored": 0, "skipped": True}


@requires_fpcalc
async def test_handle_fingerprint_computes_and_persists_matches(
    db_session: Session, tmp_path: Path
) -> None:
    audio_path = tmp_path / "silence.mp3"
    shutil.copy(FIXTURES / "silence.mp3", audio_path)
    t = _make_track(db_session, path=str(audio_path), title="T1")
    db_session.commit()

    context = WorkerContext(
        provider_set=ProviderSet(
            metadata={}, art={}, lyrics={}, fingerprint={"acoustid": _StubAcoustID()}, clients=()  # type: ignore[dict-item]
        )
    )
    job = enqueue(db_session, type="fingerprint", payload={})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_fingerprint(db_session, job, progress, context)

    assert result["fingerprinted"] == 1
    assert result["errored"] == 0
    db_session.expire_all()
    refreshed = db_session.get(Track, t.id)
    assert refreshed is not None
    assert refreshed.acoustid_fingerprint is not None
    matches = list(db_session.query(TrackFingerprintMatch).filter_by(track_id=t.id))
    assert len(matches) == 1
    assert matches[0].mb_recording_id == "rec-1"
