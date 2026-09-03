"""TEST-RESCAN-001: handler/integration with disposable audio.

- reread updates the catalog (tag edit visible)
- missing-race semantics are visible (file vanishes between checks)
- providers are not contacted by reread
- analysis/matching starts only after successful reread

Uses disposable audio fixtures isolated in tmp_path, never real music/data.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import Track
from muzilla.jobs.handlers.scan import handle_analyze_track, handle_rescan_track
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.pipeline.scan import scan_library
from muzilla.providers.set import ProviderSet
from muzilla.tags.writer import write_fields

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "audio"


def _context(tmp_path: Path, library: Path) -> WorkerContext:
    cfg = Config()
    cfg.storage.library_root = library
    cfg.storage.db_path = tmp_path / "test.db"
    return WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=cfg,
    )


def _copy_fixture(dest: Path, name: str = "silence.mp3") -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    src = FIXTURES / name
    dst = dest / name
    shutil.copy(src, dst)
    return dst


async def test_rescan_updates_catalog_with_disposable_audio(
    db_session: Session, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    p = _copy_fixture(library)
    scan_library(db_session, library)
    track = db_session.scalar(select(Track))
    assert track is not None
    assert track.title is not None

    # Edit file's tags via writer (disposable)
    write_fields(p, {"title": "Rescanned Title XYZ"})
    # Ensure mtime changes beyond filesystem granularity
    time.sleep(0.02)
    # Touch to ensure mtime changes if writer didn't already
    p.touch()

    job = enqueue(db_session, type="rescan_track", payload={"track_id": track.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)
    result = await handle_rescan_track(db_session, job, progress, _context(tmp_path, library))

    assert result["state"] == "updated"
    refreshed = db_session.get(Track, track.id)
    assert refreshed is not None
    assert refreshed.title == "Rescanned Title XYZ"
    assert refreshed.missing_since is None
    assert refreshed.probe_error is None


async def test_rescan_missing_race_visible(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    _copy_fixture(library)
    scan_library(db_session, library)
    track = db_session.scalar(select(Track))
    assert track is not None
    target = library / "silence.mp3"

    # Make exists() return True, but stat() raise FileNotFoundError (race)
    orig_is_file = Path.is_file

    def fake_is_file(self: Path) -> bool:
        if self == target:
            return True
        return orig_is_file(self)

    orig_stat = Path.stat

    def fake_stat(self: Path, *a: Any, **kw: Any) -> Any:
        if self == target:
            raise FileNotFoundError(str(self))
        return orig_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "is_file", fake_is_file)
    monkeypatch.setattr(Path, "stat", fake_stat)

    job = enqueue(db_session, type="rescan_track", payload={"track_id": track.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)
    result = await handle_rescan_track(db_session, job, progress, _context(tmp_path, library))

    assert result["state"] == "missing"
    refreshed = db_session.get(Track, track.id)
    assert refreshed is not None
    assert refreshed.missing_since is not None


async def test_rescan_does_not_contact_providers(db_session: Session, tmp_path: Path) -> None:
    library = tmp_path / "library"
    _copy_fixture(library)
    scan_library(db_session, library)
    track = db_session.scalar(select(Track))
    assert track is not None

    # Instrument provider set: any call should fail test
    cfg = Config()
    cfg.storage.library_root = library
    calls: list[str] = []

    class FakeProvider:
        async def search_releases(self, *a: Any, **kw: Any) -> Any:
            calls.append("search_releases")
            raise AssertionError("providers must not be contacted by reread")

        async def get_release(self, *a: Any, **kw: Any) -> Any:
            calls.append("get_release")
            raise AssertionError("providers must not be contacted by reread")

    # Put a fake provider in set, ensure handler doesn't use it
    provider_set = ProviderSet(
        metadata={"musicbrainz": FakeProvider()},
        art={},
        lyrics={},
        fingerprint={},
        clients=(),  # type: ignore
    )
    ctx = WorkerContext(provider_set=provider_set, config=cfg)

    job = enqueue(db_session, type="rescan_track", payload={"track_id": track.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)
    result = await handle_rescan_track(db_session, job, progress, ctx)

    assert result["state"] == "updated"
    assert calls == []


async def test_analyze_does_not_start_after_unsuccessful_reread(
    db_session: Session, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    _copy_fixture(library)
    scan_library(db_session, library)
    track = db_session.scalar(select(Track))
    assert track is not None
    # Remove file to make reread fail (missing)
    (library / "silence.mp3").unlink()

    job = enqueue(db_session, type="analyze_track", payload={"track_id": track.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)
    result = await handle_analyze_track(db_session, job, progress, _context(tmp_path, library))

    assert result["reread_state"] == "missing"
    assert result["analysis_started"] is False
    assert result["fingerprint_computed"] is False
    assert result["replaygain_attempted"] is False
    assert result["matching_attempted"] is False
    refreshed = db_session.get(Track, track.id)
    assert refreshed is not None
    assert refreshed.missing_since is not None


async def test_analyze_starts_only_after_successful_reread(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    p = _copy_fixture(library)
    scan_library(db_session, library)
    track = db_session.scalar(select(Track))
    assert track is not None
    write_fields(p, {"title": "Another Title"})
    time.sleep(0.02)
    p.touch()

    # Mock fingerprint to prove real post-reread work without needing fpcalc binary
    from muzilla.audio.fingerprint import Fingerprint

    monkeypatch.setattr(
        "muzilla.audio.fingerprint.compute_fingerprint",
        lambda path: Fingerprint(duration_s=1.23, fingerprint="fake-fp-123"),
    )

    job = enqueue(db_session, type="analyze_track", payload={"track_id": track.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)
    result = await handle_analyze_track(db_session, job, progress, _context(tmp_path, library))

    assert result["reread_state"] == "updated"
    assert result["analysis_started"] is True
    assert result["fingerprint_computed"] is True
    assert result["fingerprint"] == "fake-fp-123"
    refreshed = db_session.get(Track, track.id)
    assert refreshed is not None
    assert refreshed.acoustid_fingerprint == "fake-fp-123"


async def test_analyze_respects_metadata_auto_disabled(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    p = _copy_fixture(library)
    scan_library(db_session, library)
    track = db_session.scalar(select(Track))
    assert track is not None
    write_fields(p, {"title": "Another Title"})
    time.sleep(0.02)
    p.touch()
    # Disable metadata_auto via settings table
    from muzilla.config.schema import Config as _Config
    from muzilla.services.settings import update_enrichment_settings

    base_cfg = _Config()
    base_cfg.storage.library_root = library
    update_enrichment_settings(db_session, base_cfg.enrichment, metadata_auto=False)
    # Mock fingerprint to ensure it would be called if enabled - should not be called
    from muzilla.audio.fingerprint import Fingerprint

    called = []

    def fake_fp(path: Path) -> Fingerprint:
        called.append("fp")
        return Fingerprint(duration_s=1.0, fingerprint="should-not-be-called")

    monkeypatch.setattr("muzilla.audio.fingerprint.compute_fingerprint", fake_fp)

    # Also mock matching to ensure not called
    async def fake_match(*a: Any, **kw: Any) -> Any:
        called.append("match")
        raise AssertionError("matching should not be attempted when metadata_auto disabled")

    monkeypatch.setattr("muzilla.pipeline.matching.propose_track_candidates", fake_match)
    cfg = _Config()
    cfg.storage.library_root = library
    fake_provider = type("FakeProvider", (), {"search_releases": lambda *a, **kw: []})()
    ctx = WorkerContext(
        provider_set=ProviderSet(
            metadata={"musicbrainz": fake_provider}, art={}, lyrics={}, fingerprint={}, clients=()
        ),
        config=cfg,
    )  # type: ignore
    job = enqueue(db_session, type="analyze_track", payload={"track_id": track.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)
    result = await handle_analyze_track(db_session, job, progress, ctx)
    assert result["reread_state"] == "updated"
    assert result["analysis_started"] is True
    assert result["matching_attempted"] is False
    assert called == []  # fingerprint not called because metadata_auto disabled


async def test_analyze_respects_replaygain_auto_disabled(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    p = _copy_fixture(library)
    scan_library(db_session, library)
    track = db_session.scalar(select(Track))
    assert track is not None
    write_fields(p, {"title": "Another Title"})
    time.sleep(0.02)
    p.touch()
    from muzilla.config.schema import Config as _Config
    from muzilla.services.settings import update_enrichment_settings

    base_cfg = _Config()
    base_cfg.storage.library_root = library
    update_enrichment_settings(db_session, base_cfg.enrichment, replaygain_auto=False)
    # Mock replaygain to ensure not called
    called = []

    def fake_rg(path: Path) -> Any:
        called.append("rg")
        raise AssertionError("replaygain should not be attempted when disabled")

    monkeypatch.setattr("muzilla.audio.replaygain.compute_track_replaygain", fake_rg)
    # Mock fingerprint to avoid needing fpcalc
    from muzilla.audio.fingerprint import Fingerprint

    monkeypatch.setattr(
        "muzilla.audio.fingerprint.compute_fingerprint",
        lambda path: Fingerprint(duration_s=1.0, fingerprint="fake-fp"),
    )
    cfg = _Config()
    cfg.storage.library_root = library
    ctx = WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=cfg,
    )
    job = enqueue(db_session, type="analyze_track", payload={"track_id": track.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)
    result = await handle_analyze_track(db_session, job, progress, ctx)
    assert result["reread_state"] == "updated"
    assert result["analysis_started"] is True
    assert result["replaygain_attempted"] is False
    assert called == []


async def test_analyze_with_all_stages_enabled_runs_fingerprint_replaygain_and_matching(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    p = _copy_fixture(library)
    scan_library(db_session, library)
    track = db_session.scalar(select(Track))
    assert track is not None
    write_fields(p, {"title": "Another Title"})
    time.sleep(0.02)
    p.touch()
    # Ensure enrichment is enabled (default)
    from pathlib import Path as _Path

    from muzilla.audio.fingerprint import Fingerprint
    from muzilla.audio.replaygain import TrackReplayGain

    fp_called: list[str] = []
    rg_called: list[str] = []
    match_called: list[str] = []

    def fake_fp(path: _Path) -> Fingerprint:
        fp_called.append(str(path))
        return Fingerprint(duration_s=2.0, fingerprint="fake-fp-enabled")

    def fake_rg(path: _Path) -> TrackReplayGain:
        rg_called.append(str(path))
        return TrackReplayGain(path=_Path(path), track_gain_db=-6.0, track_peak=0.99)

    async def fake_match(*a: Any, **kw: Any) -> Any:
        match_called.append("match")

        # Return a minimal TrackMatchProposal-like object
        class FakeRes:
            candidates: tuple[Any, ...] = ()

        return FakeRes()

    monkeypatch.setattr("muzilla.audio.fingerprint.compute_fingerprint", fake_fp)
    monkeypatch.setattr("muzilla.audio.replaygain.compute_track_replaygain", fake_rg)
    monkeypatch.setattr("muzilla.pipeline.matching.propose_track_candidates", fake_match)
    # Need a provider for matching to be attempted
    from muzilla.config.schema import Config as _Config

    cfg = _Config()
    cfg.storage.library_root = library
    fake_provider = type("FakeProvider", (), {"search_releases": lambda *a, **kw: []})()
    ctx = WorkerContext(
        provider_set=ProviderSet(
            metadata={"musicbrainz": fake_provider}, art={}, lyrics={}, fingerprint={}, clients=()
        ),
        config=cfg,  # type: ignore[arg-type]
    )
    job = enqueue(db_session, type="analyze_track", payload={"track_id": track.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)
    result = await handle_analyze_track(db_session, job, progress, ctx)
    assert result["reread_state"] == "updated"
    assert result["analysis_started"] is True
    assert result["fingerprint_computed"] is True
    assert result["fingerprint"] == "fake-fp-enabled"
    assert result["replaygain_attempted"] is True
    assert result["replaygain_computed"] is True
    assert result["matching_attempted"] is True
    assert fp_called != []
    assert rg_called != []
    assert match_called != []
    refreshed = db_session.get(Track, track.id)
    assert refreshed is not None
    assert refreshed.acoustid_fingerprint == "fake-fp-enabled"
