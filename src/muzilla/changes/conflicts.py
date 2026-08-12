"""Drift detection for the apply path.

Files are the source of truth. Before writing any change, the applier
re-reads the file's current tags and recomputes
`tag_hash`; if it differs from the hash recorded when the ChangeSet was
staged, something else (Picard, foobar2000, a manual edit outside
muzilla) touched the file in the meantime, and the write must be
treated as a conflict rather than silently overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from muzilla.domain.metadata import tag_hash as compute_tag_hash
from muzilla.tags.reader import TagReadError, read_track


@dataclass(frozen=True, slots=True)
class ConflictCheck:
    conflicted: bool
    current_tag_hash: str | None
    """None if the file could not even be read (missing/corrupt) —
    also treated as a conflict, since there is nothing safe to write on
    top of."""
    error: str | None = None


def probe(path: str, expected_tag_hash: str | None) -> ConflictCheck:
    """Re-read `path` and compare its current tag_hash against
    `expected_tag_hash` (the hash recorded on the Track row when the
    ChangeSet was staged).

    A `None` expected hash (e.g. a track scanned before tag_hash existed)
    is treated as "unknown baseline" -> never conflicts, since there is
    nothing to compare against.
    """
    try:
        meta = read_track(Path(path))
    except TagReadError as exc:
        return ConflictCheck(conflicted=True, current_tag_hash=None, error=str(exc))

    current = compute_tag_hash(meta)
    if expected_tag_hash is None:
        return ConflictCheck(conflicted=False, current_tag_hash=current)

    return ConflictCheck(conflicted=current != expected_tag_hash, current_tag_hash=current)
