"""Shared scan constants — single source for audio/sidecar/ignore definitions.

Reused by pipeline/scan (the writer) and services/import_scope (preview/browse)
so preview counts and scan behaviour cannot diverge. Also provides scope
helpers for scoped import propagation.

Lower-layer shared source: pipeline and services both import from here
without creating a cycle (no muzilla imports inside this module beyond
stdlib), satisfying the duplicate-elimination requirement with a parity test.
"""

from __future__ import annotations

from pathlib import Path
from unicodedata import normalize as unicode_normalize

AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aiff", ".aif"}
)

IGNORED_SIDECAR_NAMES: frozenset[str] = frozenset(
    {"cover.jpg", "folder.jpg", "album.jpg", "cover.png", "folder.png", "album.png"}
)

DEFAULT_IGNORE_DIR_NAMES: frozenset[str] = frozenset(
    {".git", "@eaDir", "$RECYCLE.BIN", ".Trash-1000"}
)


def normalize_path(path: Path) -> str:
    """NFC-normalized absolute path string — the DB's join key."""
    return unicode_normalize("NFC", str(path.resolve()))


def is_in_scope(track_path: str, scope_root: Path) -> bool:
    """True if normalized track_path is within scope_root (file or directory).

    File scope: only the exact file matches.
    Directory scope: the file itself and any descendant.
    Comparison uses normalized absolute paths so DB-stored paths and
    resolved scope paths compare correctly across NFC/symlink variants.
    """
    try:
        scope_str = (
            normalize_path(scope_root)
            if scope_root.exists()
            else unicode_normalize("NFC", str(scope_root))
        )
    except OSError:
        scope_str = unicode_normalize("NFC", str(scope_root))
    if track_path == scope_str:
        return True
    try:
        is_file = scope_root.is_file()
    except OSError:
        is_file = False
    if is_file:
        return False
    prefix = scope_str.rstrip("/") + "/"
    return track_path.startswith(prefix)
