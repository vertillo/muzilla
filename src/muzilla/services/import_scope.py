"""Server-side browse and dry-run preview for scoped import.

Reuses scan's ignore/format constants so browse/preview stay consistent
with what scan_library will actually see. Never writes DB/files.
Bounded traversal prevents large-directory DoS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from muzilla.pipeline.scan_constants import (
    AUDIO_EXTENSIONS as _AUDIO_EXTENSIONS,
)
from muzilla.pipeline.scan_constants import (
    DEFAULT_IGNORE_DIR_NAMES as _DEFAULT_IGNORE_DIR_NAMES,
)
from muzilla.pipeline.scan_constants import (
    IGNORED_SIDECAR_NAMES as _IGNORED_SIDECAR_NAMES,
)
from muzilla.services.paths_guard import require_within_library_root

MAX_BROWSE_ENTRIES = 500
MAX_PREVIEW_FILES = 10000
MAX_PREVIEW_DIRS = 3000
MAX_UNSUPPORTED_EXAMPLES = 20


@dataclass(frozen=True, slots=True)
class BrowseEntry:
    name: str
    path: str
    kind: str  # "dir" | "file"
    is_symlink: bool = False
    symlink_target: str | None = None
    blocked: bool = False
    supported: bool | None = None
    ignored: bool = False


@dataclass(frozen=True, slots=True)
class BrowseOut:
    path: str
    parent: str | None
    entries: tuple[BrowseEntry, ...]
    truncated: bool
    library_root: str


@dataclass(frozen=True, slots=True)
class PreviewOut:
    scope_path: str
    scope_kind: str  # "file" | "directory"
    library_root: str
    supported_count: int
    unsupported_count: int
    ignored_sidecar_count: int
    excluded_dir_count: int
    symlink_excluded_count: int
    total_files_considered: int
    truncated: bool
    unsupported_examples: tuple[str, ...]
    excluded_dir_examples: tuple[str, ...]


def _resolve_contained(path_str: str, library_root: Path) -> Path:
    # Empty means library root itself.
    if not path_str:
        return library_root.resolve()
    return require_within_library_root(path_str, library_root=library_root)


def browse_import_path(path_str: str | None, library_root: Path) -> BrowseOut:
    """List directory entries contained within library_root.

    Raises ValueError for containment violation or non-directory,
    FileNotFoundError for missing path.
    """
    raw = path_str or str(library_root)
    resolved = _resolve_contained(raw, library_root)
    library_resolved = library_root.resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"{raw!r} does not exist")
    if resolved.is_file():
        raise ValueError(f"{raw!r} is a file, not a directory")

    # Symlink check: resolved already follows symlink, containment passed,
    # but we also want to block browsing via a symlink directory itself
    # that was selected as path? If resolved was a symlink to dir inside,
    # it's okay (already contained). So no extra block here.

    entries: list[BrowseEntry] = []
    truncated = False
    try:
        scandir_entries = list(os.scandir(resolved))
    except OSError as exc:
        raise ValueError(str(exc)) from exc

    # Sort dirs first, then files, alphabetically.
    def sort_key(entry: os.DirEntry[str]) -> tuple[int, str]:
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            is_dir = False
        return (0 if is_dir else 1, entry.name.lower())

    scandir_entries.sort(key=sort_key)

    for entry in scandir_entries:
        if len(entries) >= MAX_BROWSE_ENTRIES:
            truncated = True
            break
        entry_path = Path(entry.path)
        try:
            is_symlink = entry.is_symlink()
        except OSError:
            is_symlink = False

        if is_symlink:
            try:
                target_resolved = entry_path.resolve()
                inside = (
                    target_resolved.is_relative_to(library_resolved)
                    or target_resolved == library_resolved
                )
            except OSError:
                target_resolved = entry_path
                inside = False
            # Classify symlink target as dir/file for display if we can stat it,
            # but never follow it for children listing.
            try:
                is_target_dir = target_resolved.is_dir()
            except OSError:
                is_target_dir = False
            kind = "dir" if is_target_dir else "file"
            entries.append(
                BrowseEntry(
                    name=entry.name,
                    path=str(entry_path),
                    kind=kind,
                    is_symlink=True,
                    symlink_target=str(target_resolved),
                    blocked=not inside,
                    supported=None,
                    ignored=False,
                )
            )
            continue

        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if is_dir:
            blocked = entry.name in _DEFAULT_IGNORE_DIR_NAMES
            # Still list it, but mark ignored so UI can grey it.
            entries.append(
                BrowseEntry(
                    name=entry.name,
                    path=str(entry_path),
                    kind="dir",
                    is_symlink=False,
                    blocked=blocked,
                    ignored=blocked,
                )
            )
            continue

        try:
            is_file = entry.is_file(follow_symlinks=False)
        except OSError:
            continue
        if not is_file:
            continue
        lname = entry.name.lower()
        ext = Path(entry.name).suffix.lower()
        if lname in _IGNORED_SIDECAR_NAMES:
            entries.append(
                BrowseEntry(
                    name=entry.name,
                    path=str(entry_path),
                    kind="file",
                    supported=False,
                    ignored=True,
                )
            )
        elif ext in _AUDIO_EXTENSIONS:
            entries.append(
                BrowseEntry(
                    name=entry.name,
                    path=str(entry_path),
                    kind="file",
                    supported=True,
                    ignored=False,
                )
            )
        else:
            entries.append(
                BrowseEntry(
                    name=entry.name,
                    path=str(entry_path),
                    kind="file",
                    supported=False,
                    ignored=False,
                )
            )

    parent: str | None = None if resolved == library_resolved else str(resolved.parent)

    return BrowseOut(
        path=str(resolved),
        parent=parent,
        entries=tuple(entries),
        truncated=truncated,
        library_root=str(library_resolved),
    )


def preview_import_scope(path_str: str, library_root: Path) -> PreviewOut:
    """Dry-run counting for a file or directory scope. Never touches DB."""
    resolved = _resolve_contained(path_str, library_root)
    library_resolved = library_root.resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"{path_str!r} does not exist")

    # Symlink outside already rejected by _resolve_contained (resolve follows).
    # For a symlink file/dir inside, resolved is the target inside; we treat
    # that as the scope path itself. That's acceptable (symlink inside -> inside).

    if resolved.is_file():
        lname = resolved.name.lower()
        ext = resolved.suffix.lower()
        if lname in _IGNORED_SIDECAR_NAMES:
            return PreviewOut(
                scope_path=str(resolved),
                scope_kind="file",
                library_root=str(library_resolved),
                supported_count=0,
                unsupported_count=0,
                ignored_sidecar_count=1,
                excluded_dir_count=0,
                symlink_excluded_count=0,
                total_files_considered=1,
                truncated=False,
                unsupported_examples=(),
                excluded_dir_examples=(),
            )
        if ext in _AUDIO_EXTENSIONS:
            return PreviewOut(
                scope_path=str(resolved),
                scope_kind="file",
                library_root=str(library_resolved),
                supported_count=1,
                unsupported_count=0,
                ignored_sidecar_count=0,
                excluded_dir_count=0,
                symlink_excluded_count=0,
                total_files_considered=1,
                truncated=False,
                unsupported_examples=(),
                excluded_dir_examples=(),
            )
        return PreviewOut(
            scope_path=str(resolved),
            scope_kind="file",
            library_root=str(library_resolved),
            supported_count=0,
            unsupported_count=1,
            ignored_sidecar_count=0,
            excluded_dir_count=0,
            symlink_excluded_count=0,
            total_files_considered=1,
            truncated=False,
            unsupported_examples=(resolved.name,),
            excluded_dir_examples=(),
        )

    # Directory case: bounded walk, no symlink following.
    supported = 0
    unsupported = 0
    ignored_sidecar = 0
    excluded_dirs = 0
    symlink_excluded = 0
    total_files = 0
    unsupported_examples: list[str] = []
    excluded_dir_examples: list[str] = []
    truncated = False
    dirs_visited = 0

    stack: list[Path] = [resolved]
    while stack:
        current = stack.pop()
        dirs_visited += 1
        if dirs_visited > MAX_PREVIEW_DIRS:
            truncated = True
            break
        try:
            entries = list(os.scandir(current))
        except OSError:
            excluded_dirs += 1
            if len(excluded_dir_examples) < MAX_UNSUPPORTED_EXAMPLES:
                excluded_dir_examples.append(current.name)
            continue

        for entry in entries:
            if total_files > MAX_PREVIEW_FILES:
                truncated = True
                break
            # Check symlink first: never follow.
            try:
                if entry.is_symlink():
                    symlink_excluded += 1
                    continue
            except OSError:
                continue

            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if entry.name in _DEFAULT_IGNORE_DIR_NAMES:
                    excluded_dirs += 1
                    if len(excluded_dir_examples) < MAX_UNSUPPORTED_EXAMPLES:
                        excluded_dir_examples.append(entry.name)
                    continue
                stack.append(Path(entry.path))
                continue

            try:
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if not is_file:
                continue
            total_files += 1
            lname = entry.name.lower()
            ext = Path(entry.name).suffix.lower()
            if lname in _IGNORED_SIDECAR_NAMES:
                ignored_sidecar += 1
            elif ext in _AUDIO_EXTENSIONS:
                supported += 1
            else:
                unsupported += 1
                if len(unsupported_examples) < MAX_UNSUPPORTED_EXAMPLES:
                    unsupported_examples.append(entry.name)
            if total_files >= MAX_PREVIEW_FILES:
                truncated = True
                break
        if truncated:
            break

    return PreviewOut(
        scope_path=str(resolved),
        scope_kind="directory",
        library_root=str(library_resolved),
        supported_count=supported,
        unsupported_count=unsupported,
        ignored_sidecar_count=ignored_sidecar,
        excluded_dir_count=excluded_dirs,
        symlink_excluded_count=symlink_excluded,
        total_files_considered=total_files,
        truncated=truncated,
        unsupported_examples=tuple(unsupported_examples),
        excluded_dir_examples=tuple(excluded_dir_examples),
    )
