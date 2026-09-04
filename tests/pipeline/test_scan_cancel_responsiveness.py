"""Regression for PERF-SCALE-001 scan-cancellation responsiveness.

The benchmark cancels a full-library scan and requires cancel-request to
terminal `cancelled` within 2000ms on every one of 30 samples. Two
synchronous windows before the first per-file checkpoint were
cancellation-blind: the full-table existing-track preload (one 100k-row
SELECT, ~1-2s in the candidate image) and the materialized top-level
``list(os.scandir(...))`` of a 100k-entry flat directory. A cancel
arriving just after lease waited out both windows, producing the
systematic ~2.1s floor.

Guards the fix: chunked keyset preload with per-chunk checkpoints plus
lazy scandir iteration, preserving map content, walk order, and filtering.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from muzilla.db.models import Track
from muzilla.pipeline.scan import (
    _PRELOAD_BATCH_SIZE,
    ScanCancelled,
    _walk_audio_files,
    scan_library,
)


def _seed_tracks(db_session, n: int) -> None:  # type: ignore[no-untyped-def]
    db_session.bulk_save_objects(
        [
            Track(
                path=f"/music/track_{i:07d}.mp3",
                filename=f"track_{i:07d}.mp3",
                ext=".mp3",
                size_bytes=100,
                mtime_ns=i,
            )
            for i in range(n)
        ]
    )
    db_session.commit()


def _counting_execute(db_session, counter: list[str], monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    orig_execute = db_session.execute

    def counting(stmt, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "FROM tracks" in str(stmt):
            counter.append(str(stmt))
        return orig_execute(stmt, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", counting)


def test_pre_requested_cancel_skips_preload_select(db_session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """Cancel already requested before scan start must raise without any preload SELECT."""
    _seed_tracks(db_session, _PRELOAD_BATCH_SIZE + 1000)
    seen: list[str] = []
    _counting_execute(db_session, seen, monkeypatch)
    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(ScanCancelled) as exc_info:
        scan_library(db_session, root, should_cancel=lambda: True)
    assert exc_info.value.stats.scanned == 0
    assert seen == []


def test_cancel_during_preload_aborts_within_one_chunk(db_session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """Cancel arriving mid-preload aborts before the full table is loaded or marked missing."""
    _seed_tracks(db_session, 3 * _PRELOAD_BATCH_SIZE + 500)
    seen: list[str] = []
    _counting_execute(db_session, seen, monkeypatch)
    calls = {"n": 0}

    def flip() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(ScanCancelled):
        scan_library(db_session, root, should_cancel=flip)
    # Cancel observed at the 3rd checkpoint: at most 2 chunks loaded, never the full table.
    assert len(seen) <= 2
    # Abort happened before the vanished-marking phase: no missing flags written.
    assert db_session.query(Track).filter(Track.missing_since.is_not(None)).count() == 0


def test_walk_yields_without_materializing_full_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """First file must be yielded after O(1) directory reads, not a full list()."""
    library = tmp_path / "library"
    library.mkdir()
    for i in range(30):
        (library / f"t{i:03d}.mp3").touch()
    real_scandir = os.scandir
    next_calls = {"n": 0}

    class _Wrap:
        def __init__(self, it):  # type: ignore[no-untyped-def]
            self._it = it

        def __enter__(self):  # type: ignore[no-untyped-def]
            self._it.__enter__()
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return self._it.__exit__(*args)

        def __iter__(self):  # type: ignore[no-untyped-def]
            return self

        def __next__(self):  # type: ignore[no-untyped-def]
            next_calls["n"] += 1
            return next(self._it)

    monkeypatch.setattr(os, "scandir", lambda p: _Wrap(real_scandir(p)))
    gen = _walk_audio_files(library, follow_symlinks=False, ignore_dir_names=frozenset())
    try:
        first = next(gen)
        assert first.suffix == ".mp3"
        assert next_calls["n"] <= 3
        rest = list(gen)
        assert len(rest) == 29
    finally:
        gen.close()
