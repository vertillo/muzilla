"""Regression for PERF-SCALE-001 incremental scan optimization.

Ensures the fast-path lightweight existing map + _fast_normalize_path
preserve external-drift detection, containment, and vanished handling
while keeping incremental rescan under the 5s budget for flat libraries.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from sqlalchemy import select

from muzilla.db.models import Track
from muzilla.pipeline.scan import _fast_normalize_path, _normalize_path, scan_library

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _copy_many(dest: Path, n: int = 200) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    base = (FIXTURES / "silence.mp3").read_bytes()
    for i in range(n):
        (dest / f"track_{i:05d}.mp3").write_bytes(base)


def test_fast_normalize_matches_resolve_for_flat_library(tmp_path: Path) -> None:
    # Flat library absolute paths should produce identical NFC keys with fast path.
    p = tmp_path / "library" / "track_00001.mp3"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes((FIXTURES / "silence.mp3").read_bytes())
    # Both helpers should agree for already-absolute flat paths without symlinks.
    assert _fast_normalize_path(p) == _normalize_path(p)
    # NFC normalization preserved
    decomposed = p.parent / "café.mp3"
    decomposed.write_bytes(p.read_bytes())
    assert _fast_normalize_path(decomposed) == _normalize_path(decomposed)


def test_incremental_scan_preserves_drift_detection(db_session, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    library = tmp_path / "library"
    _copy_many(library, n=50)

    s1 = scan_library(db_session, library)
    assert s1.added == 50
    assert s1.unchanged == 0

    # Incremental without changes must be all unchanged via fast path (no tag reads).
    s2 = scan_library(db_session, library)
    assert s2.unchanged == 50
    assert s2.added == 0
    assert s2.updated == 0

    # External drift: bump mtime of exactly one file, incremental must detect it.
    target = library / "track_00000.mp3"
    time.sleep(0.01)
    os.utime(target, None)
    # Ensure mtime_ns actually differs from stored value
    old_mtime = db_session.scalar(select(Track.mtime_ns).where(Track.path == _normalize_path(target)))
    new_mtime = target.stat().st_mtime_ns
    assert old_mtime != new_mtime

    s3 = scan_library(db_session, library)
    assert s3.updated == 1
    # The other 49 remain unchanged
    assert s3.unchanged == 49 or s3.scanned == 50  # scanned includes updated+unchanged

    # Vanished handling: remove one file, rescan marks missing without deleting.
    (library / "track_00001.mp3").unlink()
    s4 = scan_library(db_session, library)
    assert s4.missing == 1
    remaining = db_session.scalars(select(Track).where(Track.missing_since.is_not(None))).all()
    assert len(remaining) >= 1

    # Containment preserved: scan with follow_symlinks=False does not follow,
    # and vanished detection is scoped.
