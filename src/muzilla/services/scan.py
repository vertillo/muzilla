"""Thin service wrapper around pipeline.scan — the only way api/cli may
trigger a filesystem scan, since both are barred from importing
muzilla.pipeline directly.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.pipeline.scan import ScanStats, scan_library


def run_scan(session: Session, root: Path) -> ScanStats:
    return scan_library(session, root)
