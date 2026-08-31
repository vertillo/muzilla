"""Batch collision detection for the path template engine.

Pure — operates over an already-rendered batch, no DB, no filesystem I/O
(that's the service layer's job: assemble the "existing library" paths
to compare against and pass them in). Flat mode checks every rendered
path against the entire library namespace; foldered mode checks only
within each directory, matching beets' own per-directory behavior.

Normalization: every path is bucketed by NFC+casefold (conservative) so
collisions are caught on both case-sensitive and case-insensitive
filesystems and across Unicode normal forms. Original spelling is
preserved for display. Preview never performs filesystem writes.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Collision:
    path: str
    track_ids: tuple[int, ...]
    """Every track (from the batch, or batch + existing library) sharing
    this rendered path. `path` is one representative spelling."""


def _norm(path: str) -> str:
    """Conservative normalization: NFC+casefold on every filesystem."""
    return unicodedata.normalize("NFC", path).casefold()


def find_collisions(
    rendered: Mapping[int, str],
    *,
    create_directories: bool,
    existing_library_paths: Mapping[str, int] | None = None,
    library_root: Path | str | None = None,
    case_sensitive: bool | None = None,
) -> list[Collision]:
    """`rendered` is the proposed batch: track_id -> rendered relative
    path. `existing_library_paths` is path -> track_id for every OTHER
    track in the library not already a key in `rendered` — only needed
    to detect a renamed track landing on an untouched track's current
    path. None is fine for a preview scoped to a subset where the
    caller has already excluded same-batch-only checks.

    Flat mode (create_directories=False): every path compared against
    the entire namespace (rendered union existing_library_paths) — a
    flat library shares one filename namespace, so two different
    releases of the same song colliding is a whole-library collision.

    Foldered mode: paths are grouped by dirname first, uniqueness only
    checked within each directory bucket ("foldered mode checks
    per-directory, as beets does").

    ``library_root`` and ``case_sensitive`` are retained for compatibility
    but ignored — preview uses conservative NFC+casefold on every
    filesystem and never performs filesystem I/O.
    """
    existing = existing_library_paths or {}
    _ = (library_root, case_sensitive)  # compatibility, no I/O

    if create_directories:
        return _find_collisions_foldered(rendered, existing)
    return _find_collisions_flat(rendered, existing)


def _find_collisions_flat(
    rendered: Mapping[int, str],
    existing: Mapping[str, int],
) -> list[Collision]:
    by_norm: dict[str, list[int]] = defaultdict(list)
    rep: dict[str, str] = {}
    for track_id, path in rendered.items():
        n = _norm(path)
        by_norm[n].append(track_id)
        rep.setdefault(n, path)
    existing_by_norm: dict[str, list[int]] = defaultdict(list)
    rep_existing: dict[str, str] = {}
    for path, tid in existing.items():
        n = _norm(path)
        existing_by_norm[n].append(tid)
        rep_existing.setdefault(n, path)
        rep.setdefault(n, path)

    collisions: list[Collision] = []
    for norm, track_ids in by_norm.items():
        all_ids = list(track_ids)
        for existing_track_id in existing_by_norm.get(norm, []):
            if existing_track_id not in all_ids:
                all_ids.append(existing_track_id)
        if len(all_ids) > 1:
            collisions.append(Collision(path=rep[norm], track_ids=tuple(all_ids)))
    # Also detect existing-only collisions that share normalized path with each other?
    # Not needed: existing paths are assumed unique in library; only batch vs batch and batch vs existing matter.
    return collisions


def _find_collisions_foldered(
    rendered: Mapping[int, str],
    existing: Mapping[str, int],
) -> list[Collision]:
    # Group both the proposed batch and the existing-library comparison
    # set by directory, so uniqueness is checked only within each
    # directory bucket rather than across the whole library.
    rendered_by_dir: dict[str, dict[int, str]] = defaultdict(dict)
    for track_id, path in rendered.items():
        rendered_by_dir[_norm(_dirname(path))][track_id] = path

    existing_by_dir: dict[str, dict[str, int]] = defaultdict(dict)
    for path, track_id in existing.items():
        existing_by_dir[_norm(_dirname(path))][path] = track_id

    collisions: list[Collision] = []
    all_dirs = set(rendered_by_dir) | set(existing_by_dir)
    for dirname in all_dirs:
        collisions.extend(
            _find_collisions_flat(
                rendered_by_dir.get(dirname, {}),
                existing_by_dir.get(dirname, {}),
            )
        )
    return collisions


def _dirname(path: str) -> str:
    if "/" not in path:
        return ""
    return path.rsplit("/", 1)[0]
