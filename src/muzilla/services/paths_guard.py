"""Constrains user-supplied filesystem roots to the configured library
(docs/PLAN.md §12c, step 2.7).

POST /api/scan and POST /api/imports both take a caller-supplied root
path. Authenticated, but nothing stopped an authenticated session from
pointing either at any path the container can read — the flat-library
model has exactly one legitimate root, the config already names it, and
the container only bind-mounts /music, so this closes a capability
nobody has a real use for.
"""

from __future__ import annotations

from pathlib import Path


def require_within_library_root(root: str, *, library_root: Path) -> Path:
    """Resolves `root` and raises ValueError unless it is `library_root`
    itself or a descendant of it. Returns the resolved path so callers
    don't have to resolve twice."""
    resolved_root = Path(root).resolve()
    resolved_library_root = library_root.resolve()
    if resolved_root != resolved_library_root and not resolved_root.is_relative_to(
        resolved_library_root
    ):
        raise ValueError(
            f"{root!r} is not the configured library root or a descendant of it"
        )
    return resolved_root
