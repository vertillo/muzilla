"""Native file writer for ReviewBundle operations.

This module owns per-file staging, tag/art/lyrics writing, fsync, and the
journaled no-clobber quarantine/publication boundary. Journaling is via
ReviewFileJournal tied to ApplyRun.

# ponytail: global file-system journal using ReviewFileJournal; per-file
# durability uses same-filesystem staging and journaled no-clobber renames.
"""

from __future__ import annotations

import ctypes
import errno
import os
import secrets
import stat
import sys
from collections.abc import Callable
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux requires fcntl for no-atime staging
    fcntl = None  # type: ignore[assignment]

from mutagen._util import FileThing
from sqlalchemy.orm import Session  # pyright: ignore[reportMissingImports]

from muzilla.changes.backup import BackupError, BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.conflicts import probe
from muzilla.db.models import ReviewFileJournal, Track
from muzilla.domain.metadata import tag_hash as compute_tag_hash
from muzilla.tags.hashing import partial_content_hash
from muzilla.tags.reader import read_lyrics, read_track
from muzilla.tags.writer import (
    TagWriteError,
    capture_embedded_art,
    clear_art,
    clear_lyrics,
    restore_embedded_art,
    write_art,
    write_fields,
    write_lyrics,
)

RECOVERY_RESTORED_MESSAGE = "recovery restored original tags"


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _same_case_only_spelling(source: str, destination: str) -> bool:
    if source == destination or len(source) != len(destination):
        return False
    pairs = tuple(zip(source, destination, strict=True))
    return any(left != right for left, right in pairs) and all(
        len(left.casefold()) == 1 and left.casefold() == right.casefold() for left, right in pairs
    )


def _case_only_alias_at(
    source: Path,
    destination: Path,
    source_parent_fd: int,
    destination_parent_fd: int,
) -> bool:
    if not _same_case_only_spelling(source.name, destination.name):
        return False
    source_parent = os.fstat(source_parent_fd)
    destination_parent = os.fstat(destination_parent_fd)
    if (source_parent.st_dev, source_parent.st_ino) != (
        destination_parent.st_dev,
        destination_parent.st_ino,
    ):
        return False
    entries = os.listdir(source_parent_fd)
    if source.name not in entries or destination.name in entries:
        return False
    try:
        source_entry = os.stat(source.name, dir_fd=source_parent_fd, follow_symlinks=False)
        destination_entry = os.stat(
            destination.name, dir_fd=destination_parent_fd, follow_symlinks=False
        )
    except OSError:
        return False
    return (
        stat.S_ISREG(source_entry.st_mode)
        and source_entry.st_dev == destination_entry.st_dev
        and source_entry.st_ino == destination_entry.st_ino
    )


def is_case_only_entry_alias(source: Path, destination: Path) -> bool:
    """Identify one differently-cased directory entry through its pinned parent."""
    if source == destination:
        return False
    source_parent_fd = _open_directory_chain(source.parent)
    destination_parent_fd = _open_directory_chain(destination.parent)
    try:
        return _case_only_alias_at(source, destination, source_parent_fd, destination_parent_fd)
    finally:
        os.close(destination_parent_fd)
        os.close(source_parent_fd)


def is_case_only_path_change(source: Path, destination: Path) -> bool:
    return source.parent == destination.parent and _same_case_only_spelling(
        source.name, destination.name
    )


def _rename_entry_no_replace(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source_name)
    destination_bytes = os.fsencode(destination_name)
    try:
        if sys.platform.startswith("linux"):
            rename = libc.renameat2
            rename.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename.restype = ctypes.c_int
            result = rename(
                source_parent_fd,
                source_bytes,
                destination_parent_fd,
                destination_bytes,
                1,
            )
        elif sys.platform == "darwin":
            rename = libc.renameatx_np
            rename.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename.restype = ctypes.c_int
            result = rename(
                source_parent_fd,
                source_bytes,
                destination_parent_fd,
                destination_bytes,
                0x00000004,
            )
        else:
            raise AttributeError
    except AttributeError as exc:
        raise OSError(errno.ENOTSUP, "atomic no-clobber rename is unsupported") from exc
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination_name)


def _move_no_clobber(
    source: Path,
    destination: Path,
    *,
    same_file: bool,
    library_root: Path | None = None,
    expected_source_guard: dict[str, object] | None = None,
    expected_destination_guard: dict[str, object] | None = None,
    checkpoint: Callable[[dict[str, object]], None] | None = None,
) -> None:
    if source == destination:
        return
    source_parent_fd, source_directories = _open_directory_chain_with_identity(source.parent)
    destination_parent_fd, destination_directories = _open_directory_chain_with_identity(
        destination.parent
    )
    try:
        if (
            expected_source_guard is not None
            and expected_source_guard.get("directories") != source_directories
        ):
            raise OSError(f"source directory identity changed before move: {source.parent}")
        if (
            expected_destination_guard is not None
            and expected_destination_guard.get("directories") != destination_directories
        ):
            raise OSError(
                f"destination directory identity changed before move: {destination.parent}"
            )
        try:
            source_entry = os.stat(source.name, dir_fd=source_parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise OSError(f"move source entry is unavailable: {source}") from exc
        if not stat.S_ISREG(source_entry.st_mode):
            raise OSError(f"move source is not a regular file: {source}")
        expected_file = (
            expected_source_guard.get("file") if expected_source_guard is not None else None
        )
        if isinstance(expected_file, dict) and (
            expected_file.get("device") != source_entry.st_dev
            or expected_file.get("inode") != source_entry.st_ino
        ):
            raise OSError(f"move source identity changed: {source}")

        case_alias = _case_only_alias_at(
            source, destination, source_parent_fd, destination_parent_fd
        )
        if same_file and not case_alias:
            raise OSError(errno.EEXIST, os.strerror(errno.EEXIST), str(destination))
        try:
            _rename_entry_no_replace(
                source_parent_fd, source.name, destination_parent_fd, destination.name
            )
            return
        except OSError as exc:
            if not case_alias or exc.errno != errno.EEXIST or checkpoint is None:
                raise
        if not _case_only_alias_at(source, destination, source_parent_fd, destination_parent_fd):
            raise OSError(errno.EEXIST, os.strerror(errno.EEXIST), str(destination))

        intermediate: Path | None = None
        for _ in range(8):
            candidate = source.parent / f".muzilla-case-{secrets.token_hex(12)}.tmp"
            try:
                os.stat(candidate.name, dir_fd=source_parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                intermediate = candidate
                break
        if intermediate is None:
            raise FileExistsError("could not allocate a case-rename intermediate entry")

        def persist(step: str) -> None:
            checkpoint(
                {
                    "version": 1,
                    "source": str(source),
                    "destination": str(destination),
                    "intermediate": str(intermediate),
                    "step": step,
                }
            )

        persist("first_intent")
        _rename_entry_no_replace(source_parent_fd, source.name, source_parent_fd, intermediate.name)
        os.fsync(source_parent_fd)
        moved_entry = os.stat(intermediate.name, dir_fd=source_parent_fd, follow_symlinks=False)
        if (moved_entry.st_dev, moved_entry.st_ino) != (source_entry.st_dev, source_entry.st_ino):
            raise OSError("case-rename intermediate does not match the verified source")
        persist("first_done")
        persist("final_intent")
        _rename_entry_no_replace(
            source_parent_fd, intermediate.name, destination_parent_fd, destination.name
        )
        os.fsync(destination_parent_fd)
        destination_entry = os.stat(
            destination.name, dir_fd=destination_parent_fd, follow_symlinks=False
        )
        if (destination_entry.st_dev, destination_entry.st_ino) != (
            source_entry.st_dev,
            source_entry.st_ino,
        ):
            raise OSError("case-rename destination does not match the verified source")
        persist("final_done")
    finally:
        os.close(destination_parent_fd)
        os.close(source_parent_fd)


def _meta_to_field_dict(track: Track) -> dict[str, Any]:
    from dataclasses import fields as dataclass_fields

    from muzilla.domain.metadata import TrackMeta

    payload: dict[str, Any] = {}
    for f in dataclass_fields(TrackMeta):
        if f.name in ("duration_ms", "bitrate", "sample_rate", "channels", "codec"):
            continue
        value = getattr(track, f.name, None)
        payload[f.name] = list(value) if isinstance(value, tuple) else value  # pyright: ignore[reportUnknownArgumentType]
    return payload


def _partial_content_hash_fd(file_fd: int, size_bytes: int) -> str:
    from hashlib import blake2b

    hasher = blake2b()
    head = os.pread(file_fd, 64 * 1024, 0)
    hasher.update(head)
    if size_bytes > 64 * 1024:
        hasher.update(os.pread(file_fd, 64 * 1024, max(size_bytes - 64 * 1024, len(head))))
    hasher.update(str(size_bytes).encode())
    return hasher.hexdigest()


def _update_track_file_facts(
    track: Track, path: Path, *, tag_hash: str, file_fd: int | None = None
) -> None:
    file_stat = os.fstat(file_fd) if file_fd is not None else path.stat()
    track.size_bytes = file_stat.st_size
    track.mtime_ns = file_stat.st_mtime_ns
    track.content_hash = (
        _partial_content_hash_fd(file_fd, file_stat.st_size)
        if file_fd is not None
        else partial_content_hash(path, file_stat.st_size)
    )
    track.tag_hash = tag_hash
    track.missing_since = None


def _persist_file_journal_failure(
    session: Session,
    journal_id: int,
    error: str,
    *,
    recovery_required: bool,
) -> tuple[bool, str]:
    try:
        session.rollback()
    except Exception as rollback_error:
        return (
            False,
            f"recovery_required: could not roll back DB session after {error}: {rollback_error}",
        )
    journal = session.get(ReviewFileJournal, journal_id)
    if journal is None:
        return False, f"recovery_required: file journal {journal_id} is unavailable"
    journal.state = "writing" if recovery_required else "failed"
    journal.error = error
    try:
        session.commit()
    except Exception:
        session.rollback()
        persisted = session.get(ReviewFileJournal, journal_id)
        if persisted is not None and persisted.state == "writing":
            return False, f"recovery_required: {error}"
        return False, f"recovery_required: journal checkpoint failed after {error}"
    if recovery_required:
        return False, f"recovery_required: {error}"
    return False, error


def _source_guard_error(path: Path, library_root: Path | None) -> str | None:
    if not path.exists():
        return f"source file no longer exists: {path}"
    if path.is_symlink():
        return f"refusing to follow symlink source: {path}"
    if library_root is None:
        return None
    resolved_root = library_root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        return f"refusing to read outside library root: {path}"
    for parent in path.parents:
        if parent.resolve() == resolved_root:
            break
        if parent.is_symlink():
            return f"refusing to follow symlink source ancestry: {parent}"
    return None


class SourcePrecondition:
    path: str
    size_bytes: int
    mtime_ns: int
    tag_hash: str

    def __init__(self, path: str, size_bytes: int, mtime_ns: int, tag_hash: str) -> None:
        self.path = path
        self.size_bytes = size_bytes
        self.mtime_ns = mtime_ns
        self.tag_hash = tag_hash


def _source_precondition_error(
    track: Track,
    expected: SourcePrecondition | None,
    *,
    library_root: Path | None,
) -> str | None:
    if expected is None:
        return None
    path = Path(track.path)
    guard_error = _source_guard_error(path, library_root)
    if guard_error is not None:
        return guard_error
    if track.path != expected.path:
        return "source snapshot path no longer matches the catalog"
    try:
        source_stat = path.stat()
    except OSError as exc:
        return f"source snapshot stat failed: {exc}"
    # tag_hash is authoritative; after rollback mtime/size may differ but hash matches
    conflict = probe(str(path), expected.tag_hash)
    # REVIEW-CONFLICTS-001: any external drift (mtime/size or tag) blocks whole bundle.
    # ponytail: stat check even when hash matches to catch touch(1) preserving tags.
    if source_stat.st_size != expected.size_bytes or source_stat.st_mtime_ns != expected.mtime_ns:
        return "source snapshot stat changed after review"
    if not conflict.conflicted:
        return None
    return conflict.error or "source snapshot tag hash changed after review"


def _open_directory_chain_with_identity(
    path: Path,
) -> tuple[int, list[dict[str, object]]]:
    """Open every directory component without following symlinks and record identity."""
    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute.anchor, flags)
    current = Path(absolute.anchor)
    first = os.fstat(descriptor)
    identities: list[dict[str, object]] = [
        {"path": str(current), "device": first.st_dev, "inode": first.st_ino}
    ]
    try:
        for component in absolute.parts[1:]:
            child = os.open(component, flags | nofollow, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            current /= component
            identity = os.fstat(descriptor)
            identities.append(
                {"path": str(current), "device": identity.st_dev, "inode": identity.st_ino}
            )
        return descriptor, identities
    except BaseException:
        os.close(descriptor)
        raise


def _open_directory_chain(path: Path) -> int:
    descriptor, _ = _open_directory_chain_with_identity(path)
    return descriptor


def _open_target_parent(
    path: Path, library_root: Path | None
) -> tuple[Path, str, int, list[dict[str, object]]]:
    target = Path(os.path.abspath(path))
    if library_root is None:
        parent_fd, identities = _open_directory_chain_with_identity(target.parent)
        return target, target.name, parent_fd, identities

    root = Path(os.path.abspath(library_root))
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise OSError(f"refusing to write outside library root: {path}") from exc
    if not relative.parts or relative.name in {".", ".."}:
        raise OSError(f"invalid file path inside library root: {path}")
    parent_fd, identities = _open_directory_chain_with_identity(root)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    current = root
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, flags | nofollow, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child
            current /= component
            identity = os.fstat(parent_fd)
            identities.append(
                {"path": str(current), "device": identity.st_dev, "inode": identity.st_ino}
            )
        return target, relative.name, parent_fd, identities
    except BaseException:
        os.close(parent_fd)
        raise


def _physical_file_guard(
    file_fd: int,
    *,
    path: Path,
    library_root: Path | None,
    directory_identities: list[dict[str, object]],
    parent_fd: int,
    name: str,
) -> dict[str, object]:
    before = os.fstat(file_fd)
    entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_dev != entry.st_dev
        or before.st_ino != entry.st_ino
    ):
        raise OSError(f"physical file entry is not a stable regular file: {path}")
    digest = sha256()
    offset = 0
    while chunk := os.pread(file_fd, 1024 * 1024, offset):
        digest.update(chunk)
        offset += len(chunk)
    after = os.fstat(file_fd)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or stat.S_IMODE(before.st_mode) != stat.S_IMODE(after.st_mode)
    ):
        raise OSError(f"physical file changed while its restoration guard was recorded: {path}")
    current_target, current_name, current_fd, current_identities = _open_target_parent(
        path, library_root
    )
    try:
        current_entry = os.stat(current_name, dir_fd=current_fd, follow_symlinks=False)
        if (
            current_target != path
            or current_identities != directory_identities
            or current_entry.st_dev != after.st_dev
            or current_entry.st_ino != after.st_ino
            or not _StagedReplacement._same_identity(os.fstat(parent_fd), os.fstat(current_fd))
        ):
            raise OSError(f"physical file path or ancestry changed while verifying: {path}")
    finally:
        os.close(current_fd)
    return {
        "version": 1,
        "path": str(path),
        "library_root": (
            str(Path(os.path.abspath(library_root))) if library_root is not None else None
        ),
        "directories": [dict(identity) for identity in directory_identities],
        "file": {
            "device": after.st_dev,
            "inode": after.st_ino,
            "mode": stat.S_IMODE(after.st_mode),
            "size": after.st_size,
            "mtime_ns": after.st_mtime_ns,
            "sha256": digest.hexdigest(),
        },
    }


def capture_file_guard(path: Path, library_root: Path | None) -> dict[str, object]:
    """Capture persisted ancestry, regular-file identity, attributes and full content."""
    target, name, parent_fd, identities = _open_target_parent(path, library_root)
    file_fd: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(name, flags, dir_fd=parent_fd)
        return _physical_file_guard(
            file_fd,
            path=target,
            library_root=library_root,
            directory_identities=identities,
            parent_fd=parent_fd,
            name=name,
        )
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def verify_file_guard(
    path: Path,
    expected_guard: dict[str, object],
    library_root: Path | None,
) -> dict[str, object]:
    if expected_guard.get("version") != 1:
        raise OSError(f"physical restoration evidence is missing or unsupported: {path}")
    actual = capture_file_guard(path, library_root)
    if actual != expected_guard:
        raise OSError(f"physical file drift or identity change detected before restore: {path}")
    return actual


def journal_file_guard(before_blob: dict[str, Any], name: str) -> dict[str, object]:
    raw = before_blob.get(name)
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise OSError(f"recovery_required: physical file evidence {name!r} is unavailable")
    return {str(key): value for key, value in raw.items()}


def verify_directory_guard(
    path: Path, expected_guard: dict[str, object], library_root: Path | None
) -> None:
    target = Path(os.path.abspath(path))
    expected_path = expected_guard.get("path")
    expected_root = expected_guard.get("library_root")
    expected_directories = expected_guard.get("directories")
    expected_root_path = (
        str(Path(os.path.abspath(library_root))) if library_root is not None else None
    )
    if (
        expected_path != str(target)
        or expected_root != expected_root_path
        or not isinstance(expected_directories, list)
    ):
        raise OSError(f"destination restoration evidence is missing or incompatible: {target}")
    _, _, parent_fd, actual_directories = _open_target_parent(target, library_root)
    try:
        if actual_directories != expected_directories:
            raise OSError(f"destination directory ancestry changed before restore: {target.parent}")
    finally:
        os.close(parent_fd)


def move_file_with_guard(
    source: Path,
    destination: Path,
    *,
    expected_source_guard: dict[str, object],
    library_root: Path | None,
    expected_destination_guard: dict[str, object] | None = None,
    checkpoint: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    if source == destination:
        raise OSError("move source and destination are the same path")
    verify_file_guard(source, expected_source_guard, library_root)
    if expected_destination_guard is not None:
        verify_directory_guard(destination, expected_destination_guard, library_root)
    try:
        same_file = source.samefile(destination)
    except OSError:
        same_file = False
    _move_no_clobber(
        source,
        destination,
        same_file=same_file,
        library_root=library_root,
        expected_source_guard=expected_source_guard,
        expected_destination_guard=expected_destination_guard,
        checkpoint=checkpoint,
    )
    _fsync_directory(destination.parent)
    if source.parent != destination.parent:
        _fsync_directory(source.parent)
    actual = capture_file_guard(destination, library_root)
    expected_file = expected_source_guard.get("file")
    if not isinstance(expected_file, dict) or actual.get("file") != expected_file:
        raise OSError(f"physical file changed during move: {destination}")
    return actual


def reconcile_case_only_move_to_source(
    source: Path,
    destination: Path,
    *,
    expected_source_guard: dict[str, object],
    library_root: Path | None,
    intermediate: Path | None = None,
    checkpoint: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Restore an interrupted case-only move to its recorded source spelling."""
    if not _same_case_only_spelling(source.name, destination.name):
        raise OSError("interrupted move is not a case-only filename change")
    source_parent_fd, source_directories = _open_directory_chain_with_identity(source.parent)
    destination_parent_fd, destination_directories = _open_directory_chain_with_identity(
        destination.parent
    )
    try:
        source_parent = os.fstat(source_parent_fd)
        destination_parent = os.fstat(destination_parent_fd)
        if (
            (source_parent.st_dev, source_parent.st_ino)
            != (destination_parent.st_dev, destination_parent.st_ino)
            or source_directories != expected_source_guard.get("directories")
            or destination_directories != source_directories
        ):
            raise OSError("case-only move parent identity changed during recovery")
        entry_names = set(os.listdir(source_parent_fd))
    finally:
        os.close(destination_parent_fd)
        os.close(source_parent_fd)

    known_names = [source.name, destination.name]
    if intermediate is not None:
        intermediate = Path(os.path.abspath(intermediate))
        if intermediate.parent != source.parent or intermediate.name in known_names:
            raise OSError("case-only move journal has an invalid intermediate path")
        known_names.append(intermediate.name)
    present = [name for name in known_names if name in entry_names]
    if len(present) != 1:
        raise OSError("case-only move recovery cannot identify exactly one journaled entry")
    actual_path = source.parent / present[0]
    actual_guard = capture_file_guard(actual_path, library_root)
    if (
        actual_guard.get("file") != expected_source_guard.get("file")
        or actual_guard.get("directories") != expected_source_guard.get("directories")
        or actual_guard.get("library_root") != expected_source_guard.get("library_root")
    ):
        raise OSError(f"case-only move recovery found changed file evidence: {actual_path}")
    if actual_path.name == source.name:
        if actual_guard != expected_source_guard:
            raise OSError(f"case-only move source spelling has changed: {source}")
        return actual_guard

    restored = move_file_with_guard(
        actual_path,
        source,
        expected_source_guard=actual_guard,
        expected_destination_guard=expected_source_guard,
        library_root=library_root,
        checkpoint=checkpoint,
    )
    if restored != expected_source_guard:
        raise OSError(f"case-only move did not restore the recorded source spelling: {source}")
    return restored


_PRIVATE_REPLACEMENT_ROOT = ".muzilla-private"
_PUBLICATION_TRANSITION_KEY = "__muzilla_publication_transition"


def _enable_linux_noatime(file_fd: int) -> None:
    if sys.platform != "linux":
        return
    noatime_flag = getattr(os, "O_NOATIME", None)
    if fcntl is None or noatime_flag is None:
        raise OSError(errno.EOPNOTSUPP, "Linux O_NOATIME support is unavailable")
    status_flags = fcntl.fcntl(file_fd, fcntl.F_GETFL)
    fcntl.fcntl(file_fd, fcntl.F_SETFL, status_flags | noatime_flag)
    if not fcntl.fcntl(file_fd, fcntl.F_GETFL) & noatime_flag:
        raise OSError(errno.EOPNOTSUPP, "Linux O_NOATIME could not be enabled")


def _file_descriptor_evidence(file_fd: int) -> dict[str, object]:
    before = os.fstat(file_fd)
    if not stat.S_ISREG(before.st_mode):
        raise OSError("replacement entry is not a regular file")
    digest = sha256()
    offset = 0
    while chunk := os.pread(file_fd, 1024 * 1024, offset):
        digest.update(chunk)
        offset += len(chunk)
    after = os.fstat(file_fd)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or stat.S_IMODE(before.st_mode) != stat.S_IMODE(after.st_mode)
    ):
        raise OSError("replacement file changed while its evidence was recorded")
    return {
        "device": after.st_dev,
        "inode": after.st_ino,
        "mode": stat.S_IMODE(after.st_mode),
        "size": after.st_size,
        "mtime_ns": after.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def _directory_identity(file_fd: int) -> dict[str, object]:
    info = os.fstat(file_fd)
    if not stat.S_ISDIR(info.st_mode):
        raise OSError("private replacement namespace is not a directory")
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
    }


class _StagedReplacement:
    """Stage, quarantine, and publish a replacement through pinned descriptors."""

    def __init__(
        self,
        path: Path,
        library_root: Path | None,
        *,
        expected_guard: dict[str, object] | None = None,
        checkpoint: Callable[[dict[str, object]], None] | None = None,
        purpose: str = "apply",
    ) -> None:
        self.path, self.name, self.parent_fd, self.directory_identities = _open_target_parent(
            path, library_root
        )
        self.library_root = library_root
        self.expected_guard = expected_guard
        self.checkpoint = checkpoint
        self.purpose = purpose
        self.private_root_fd: int | None = None
        self.private_fd: int | None = None
        self.private_root_identity: dict[str, object] | None = None
        self.private_identity: dict[str, object] | None = None
        self.operation_name: str | None = None
        self.source_fd: int | None = None
        self.temp_fd: int | None = None
        self.temp_name: str | None = None
        self.staged_name: str | None = None
        self.holding_name: str | None = None
        self.withdrawn_name: str | None = None
        self.fileobj: Any | None = None
        self.source_stat: os.stat_result | None = None
        self.source_file_evidence: dict[str, object] | None = None
        self.staged_evidence: dict[str, object] | None = None
        self.published_guard: dict[str, object] | None = None
        self.published = False
        self.publication_uncertain = False
        self.transition_started = False
        self.preserve_private_entries = False
        self.finalized = False
        try:
            source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            self.source_fd = os.open(self.name, source_flags, dir_fd=self.parent_fd)
            self.source_stat = os.fstat(self.source_fd)
            if not stat.S_ISREG(self.source_stat.st_mode):
                raise OSError(f"source is not a regular file: {self.path}")
            entry_stat = os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False)
            if not self._same_identity(self.source_stat, entry_stat):
                raise OSError(f"source changed while opening: {self.path}")
            self.source_file_evidence = _file_descriptor_evidence(self.source_fd)

            legacy_temp = f"{self.name}.muzilla.tmp"
            try:
                os.stat(legacy_temp, dir_fd=self.parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(f"reserved temporary path already exists: {legacy_temp}")

            self._verify_parent(library_root)
            self._open_private_namespace()
            self._create_temp()
            assert self.temp_fd is not None
            self.fileobj = os.fdopen(os.dup(self.temp_fd), "r+b", buffering=0)
            self._copy_source()
            if expected_guard is not None:
                self._verify_source_guard(expected_guard, library_root)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
        return left.st_dev == right.st_dev and left.st_ino == right.st_ino

    @property
    def filething(self) -> FileThing:
        assert self.fileobj is not None
        return FileThing(self.fileobj, str(self.path), str(self.path))

    def _verify_parent(self, library_root: Path | None) -> None:
        current_target, _, current_fd, current_identities = _open_target_parent(
            self.path, library_root
        )
        try:
            if (
                current_target != self.path
                or current_identities != self.directory_identities
                or not self._same_identity(os.fstat(self.parent_fd), os.fstat(current_fd))
            ):
                raise OSError(f"source directory changed during write: {self.path.parent}")
        finally:
            os.close(current_fd)

    def _verify_source_guard(
        self, expected_guard: dict[str, object], library_root: Path | None
    ) -> None:
        assert self.source_fd is not None
        actual = _physical_file_guard(
            self.source_fd,
            path=self.path,
            library_root=library_root,
            directory_identities=self.directory_identities,
            parent_fd=self.parent_fd,
            name=self.name,
        )
        if actual != expected_guard:
            raise OSError(f"physical file drift or identity change detected: {self.path}")

    def _open_private_namespace(self) -> None:
        assert self.parent_fd is not None
        # Keep this shared-library anchor; only private per-operation children are removed.
        root_name = _PRIVATE_REPLACEMENT_ROOT
        try:
            os.mkdir(root_name, 0o700, dir_fd=self.parent_fd)
            os.fsync(self.parent_fd)
        except FileExistsError:
            pass
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        self.private_root_fd = os.open(root_name, flags | nofollow, dir_fd=self.parent_fd)
        root_stat = os.fstat(self.private_root_fd)
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or root_stat.st_dev != os.fstat(self.parent_fd).st_dev
            or stat.S_IMODE(root_stat.st_mode) != 0o700
            or (hasattr(os, "geteuid") and root_stat.st_uid != os.geteuid())
        ):
            raise OSError(f"unsafe private replacement namespace: {self.path.parent / root_name}")
        root_entry = os.stat(root_name, dir_fd=self.parent_fd, follow_symlinks=False)
        if not self._same_identity(root_stat, root_entry):
            raise OSError("private replacement namespace changed while opening")
        self.private_root_identity = _directory_identity(self.private_root_fd)
        for _ in range(8):
            operation = secrets.token_hex(16)
            try:
                os.mkdir(operation, 0o700, dir_fd=self.private_root_fd)
            except FileExistsError:
                continue
            self.operation_name = operation
            break
        if self.operation_name is None:
            raise FileExistsError("could not allocate private replacement namespace")
        self.private_fd = os.open(
            self.operation_name, flags | nofollow, dir_fd=self.private_root_fd
        )
        private_stat = os.fstat(self.private_fd)
        if (
            not stat.S_ISDIR(private_stat.st_mode)
            or private_stat.st_dev != root_stat.st_dev
            or stat.S_IMODE(private_stat.st_mode) != 0o700
            or (hasattr(os, "geteuid") and private_stat.st_uid != os.geteuid())
        ):
            raise OSError("unsafe per-operation replacement namespace")
        self.private_identity = _directory_identity(self.private_fd)
        os.fsync(self.private_root_fd)

    def _create_temp(self) -> None:
        assert self.private_fd is not None
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        self.temp_name = "staged"
        self.staged_name = self.temp_name
        self.holding_name = "original"
        self.withdrawn_name = "withdrawn"
        self.temp_fd = os.open(self.temp_name, flags, 0o600, dir_fd=self.private_fd)
        # Only this exclusively created replacement is ours to mark no-atime.
        _enable_linux_noatime(self.temp_fd)

    def _copy_source(self) -> None:
        assert self.source_fd is not None and self.temp_fd is not None
        assert self.source_stat is not None
        source_file = os.fdopen(os.dup(self.source_fd), "rb", buffering=0)
        temp_file = os.fdopen(os.dup(self.temp_fd), "wb", buffering=0)
        try:
            while chunk := source_file.read(1024 * 1024):
                view = memoryview(chunk)
                while view:
                    written = temp_file.write(view)
                    if written is None or written <= 0:
                        raise OSError("short write while staging source file")
                    view = view[written:]
            os.fchmod(self.temp_fd, stat.S_IMODE(self.source_stat.st_mode))
            os.utime(
                self.temp_fd,
                ns=(self.source_stat.st_atime_ns, self.source_stat.st_mtime_ns),
            )
            os.fsync(self.temp_fd)
        finally:
            source_file.close()
            temp_file.close()

    def _transition_record(self, phase: str, **extra: object) -> dict[str, object]:
        assert self.operation_name is not None
        assert self.private_root_identity is not None and self.private_identity is not None
        assert self.staged_name is not None and self.holding_name is not None
        record: dict[str, object] = {
            "version": 1,
            "purpose": self.purpose,
            "phase": phase,
            "path": str(self.path),
            "library_root": (
                str(Path(os.path.abspath(self.library_root)))
                if self.library_root is not None
                else None
            ),
            "parent_directories": [dict(identity) for identity in self.directory_identities],
            "source_guard": dict(self.expected_guard) if self.expected_guard is not None else None,
            "private_root_name": _PRIVATE_REPLACEMENT_ROOT,
            "private_root_identity": dict(self.private_root_identity),
            "operation_name": self.operation_name,
            "private_identity": dict(self.private_identity),
            "staged_name": self.staged_name,
            "holding_name": self.holding_name,
            "withdrawn_name": self.withdrawn_name,
            "staged_file": dict(self.staged_evidence) if self.staged_evidence is not None else None,
        }
        record.update(extra)
        return record

    def _checkpoint(self, phase: str, **extra: object) -> None:
        record = self._transition_record(phase, **extra)
        if self.checkpoint is not None:
            self.checkpoint(record)
        self.transition_started = phase not in {"stage_ready", "compensated", "recovered"}
        self.preserve_private_entries = self.transition_started or phase == "published"

    def verify_for_publish(self, library_root: Path | None) -> None:
        assert self.source_fd is not None and self.temp_fd is not None
        assert self.source_stat is not None and self.temp_name is not None
        assert self.private_fd is not None
        self._verify_parent(library_root)
        current_source = os.fstat(self.source_fd)
        source_entry = os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False)
        temp_fd_stat = os.fstat(self.temp_fd)
        temp_entry = os.stat(self.temp_name, dir_fd=self.private_fd, follow_symlinks=False)
        if not self._same_identity(self.source_stat, current_source):
            raise OSError(f"source identity changed while staging: {self.path}")
        if not self._same_identity(self.source_stat, source_entry):
            raise OSError(f"source path was replaced while staging: {self.path}")
        if not stat.S_ISREG(temp_entry.st_mode) or not self._same_identity(
            temp_fd_stat, temp_entry
        ):
            raise OSError(f"staged file was replaced before publication: {self.path}")
        if self.expected_guard is not None:
            self._verify_source_guard(self.expected_guard, library_root)

    def _entry_identity(self, parent_fd: int, name: str) -> dict[str, object]:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        identity: dict[str, object] = {
            "device": entry.st_dev,
            "inode": entry.st_ino,
            "mode": entry.st_mode,
            "size": entry.st_size,
            "mtime_ns": entry.st_mtime_ns,
        }
        if stat.S_ISREG(entry.st_mode):
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(name, flags, dir_fd=parent_fd)
            try:
                opened = os.fstat(fd)
                if not self._same_identity(entry, opened):
                    raise OSError(f"replacement entry changed while inspecting: {name}")
                identity["file"] = _file_descriptor_evidence(fd)
            finally:
                os.close(fd)
        return identity

    def _held_matches_source_guard(self) -> bool:
        assert self.private_fd is not None and self.holding_name is not None
        try:
            evidence = self._entry_identity(self.private_fd, self.holding_name)
        except (FileNotFoundError, OSError):
            return False
        expected_file = (
            self.expected_guard.get("file")
            if self.expected_guard is not None
            else self.source_file_evidence
        )
        actual_file = evidence.get("file")
        return isinstance(expected_file, dict) and actual_file == expected_file

    def _try_restore_holding(self) -> bool:
        assert self.private_fd is not None and self.holding_name is not None
        assert self.parent_fd is not None
        try:
            held_identity = self._entry_identity(self.private_fd, self.holding_name)
            self._checkpoint("compensation_intent", held_entry=held_identity)
            _rename_entry_no_replace(self.private_fd, self.holding_name, self.parent_fd, self.name)
            os.fsync(self.private_fd)
            os.fsync(self.parent_fd)
            restored = self._entry_identity(self.parent_fd, self.name)
            if all(
                restored.get(key) == held_identity.get(key)
                for key in ("device", "inode", "mode", "size", "mtime_ns")
            ):
                self._checkpoint("compensated", compensated_entry=restored)
                self.publication_uncertain = False
                self.preserve_private_entries = False
                self.transition_started = False
                return True
            self.publication_uncertain = True
        except OSError:
            self.publication_uncertain = True
            with suppress(Exception):
                self._checkpoint("compensation_blocked")
        return False

    def capture_published_guard(self, library_root: Path | None) -> dict[str, object]:
        assert self.temp_fd is not None
        return _physical_file_guard(
            self.temp_fd,
            path=self.path,
            library_root=library_root,
            directory_identities=self.directory_identities,
            parent_fd=self.parent_fd,
            name=self.name,
        )

    def publish(self, library_root: Path | None) -> None:
        assert self.temp_name is not None and self.temp_fd is not None
        assert self.fileobj is not None and self.source_stat is not None
        assert self.private_fd is not None and self.holding_name is not None
        self.fileobj.flush()
        os.fchmod(self.temp_fd, stat.S_IMODE(self.source_stat.st_mode))
        os.utime(
            self.temp_fd,
            ns=(self.source_stat.st_atime_ns, self.source_stat.st_mtime_ns),
        )
        os.fsync(self.temp_fd)
        self.staged_evidence = _file_descriptor_evidence(self.temp_fd)
        staged_entry = os.stat(self.temp_name, dir_fd=self.private_fd, follow_symlinks=False)
        if not self._same_identity(os.fstat(self.temp_fd), staged_entry):
            raise OSError("staged replacement changed before publication")
        os.fsync(self.private_fd)
        self.verify_for_publish(library_root)
        self._checkpoint("stage_ready")
        self._checkpoint("displace_intent")
        self.transition_started = True
        self.preserve_private_entries = True
        try:
            _rename_entry_no_replace(self.parent_fd, self.name, self.private_fd, self.holding_name)
            os.fsync(self.parent_fd)
            os.fsync(self.private_fd)
        except OSError:
            self.publication_uncertain = True
            raise
        self.publication_uncertain = True
        try:
            held_entry = self._entry_identity(self.private_fd, self.holding_name)
        except OSError:
            self.publication_uncertain = True
            raise
        self._checkpoint("displaced", held_entry=held_entry)
        if not self._held_matches_source_guard():
            restored = self._try_restore_holding()
            if not restored:
                self.publication_uncertain = True
                self.preserve_private_entries = True
                raise OSError("recovery_required: quarantined source was not the reviewed file")
            raise OSError(
                "source file changed immediately before quarantine; foreign entry restored"
            )
        self._checkpoint("displacement_verified", held_entry=held_entry)
        self._checkpoint("publication_intent")
        try:
            _rename_entry_no_replace(self.private_fd, self.temp_name, self.parent_fd, self.name)
            self.temp_name = None
            os.fsync(self.private_fd)
            os.fsync(self.parent_fd)
        except OSError:
            self.publication_uncertain = True
            self.preserve_private_entries = True
            with suppress(Exception):
                self._try_restore_holding()
            raise
        self.published = True
        self.publication_uncertain = True
        try:
            self.published_guard = self.capture_published_guard(library_root)
            published_file = self.published_guard.get("file")
            if published_file != self.staged_evidence:
                raise OSError("published entry does not match the durably staged replacement")
            self._checkpoint("published", published_guard=self.published_guard)
            self.publication_uncertain = False
        except Exception:
            self.publication_uncertain = True
            self.preserve_private_entries = True
            raise

    def complete_transition(self) -> dict[str, object] | None:
        if self.published_guard is None:
            return None
        return self._transition_record("complete", published_guard=self.published_guard)

    def finalize_after_durable_outcome(self) -> None:
        """Release the held original only after the caller committed its inverse."""
        if not self.published or self.expected_guard is None:
            return
        if not self._public_private_root_matches():
            return
        assert self.private_fd is not None and self.holding_name is not None
        assert self.published_guard is not None
        try:
            current = capture_file_guard(self.path, self.library_root)
            if current != self.published_guard or not self._held_matches_source_guard():
                return
            os.unlink(self.holding_name, dir_fd=self.private_fd)
            os.fsync(self.private_fd)
            self._remove_empty_operation_directory()
            self.finalized = True
            self.preserve_private_entries = False
        except OSError:
            return

    def _public_private_root_matches(self) -> bool:
        assert self.private_root_fd is not None
        try:
            current_parent_fd = _open_directory_chain(self.path.parent)
            try:
                entry = os.stat(
                    _PRIVATE_REPLACEMENT_ROOT,
                    dir_fd=current_parent_fd,
                    follow_symlinks=False,
                )
                return self._same_identity(entry, os.fstat(self.private_root_fd))
            finally:
                os.close(current_parent_fd)
        except OSError:
            return False

    def _remove_empty_operation_directory(self) -> None:
        assert self.private_root_fd is not None and self.private_fd is not None
        assert self.operation_name is not None
        try:
            if os.listdir(self.private_fd):
                return
            entry = os.stat(
                self.operation_name,
                dir_fd=self.private_root_fd,
                follow_symlinks=False,
            )
            if not self._same_identity(entry, os.fstat(self.private_fd)):
                return
            os.rmdir(self.operation_name, dir_fd=self.private_root_fd)
            os.fsync(self.private_root_fd)
        except OSError:
            return

    def close(self) -> None:
        if self.fileobj is not None:
            self.fileobj.close()
            self.fileobj = None
        if not self.preserve_private_entries and not self.published and self.private_fd is not None:
            if self.temp_name is not None and self.temp_fd is not None:
                try:
                    entry = os.stat(self.temp_name, dir_fd=self.private_fd, follow_symlinks=False)
                    if self._same_identity(os.fstat(self.temp_fd), entry):
                        os.unlink(self.temp_name, dir_fd=self.private_fd)
                        os.fsync(self.private_fd)
                except FileNotFoundError:
                    pass
                self.temp_name = None
            self._remove_empty_operation_directory()
        for name in ("temp_fd", "source_fd", "private_fd", "private_root_fd", "parent_fd"):
            descriptor = getattr(self, name)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)

    def __enter__(self) -> _StagedReplacement:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def _replacement_entry_evidence(parent_fd: int, name: str) -> dict[str, object] | None:
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    evidence: dict[str, object] = {
        "device": entry.st_dev,
        "inode": entry.st_ino,
        "mode": entry.st_mode,
        "size": entry.st_size,
        "mtime_ns": entry.st_mtime_ns,
    }
    if stat.S_ISREG(entry.st_mode):
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(name, flags, dir_fd=parent_fd)
        try:
            opened = os.fstat(file_fd)
            if not _StagedReplacement._same_identity(entry, opened):
                raise OSError(f"replacement entry changed while inspecting: {name}")
            evidence["file"] = _file_descriptor_evidence(file_fd)
        finally:
            os.close(file_fd)
    return evidence


def _safe_private_entry_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise OSError("replacement journal contains an invalid private entry name")
    return value


def _open_replacement_namespace(
    path: Path,
    transition: dict[str, object],
    library_root: Path | None,
) -> tuple[int, int, int, str, str, str]:
    target = Path(os.path.abspath(path))
    root_expected = transition.get("library_root")
    expected_root = str(Path(os.path.abspath(library_root))) if library_root is not None else None
    parent_expected = transition.get("parent_directories")
    if (
        transition.get("version") != 1
        or transition.get("path") != str(target)
        or root_expected != expected_root
        or not isinstance(parent_expected, list)
        or transition.get("private_root_name") != _PRIVATE_REPLACEMENT_ROOT
    ):
        raise OSError("replacement recovery evidence is missing or incompatible")
    _, source_name, parent_fd, actual_parent = _open_target_parent(target, library_root)
    root_fd: int | None = None
    operation_fd: int | None = None
    try:
        if actual_parent != parent_expected:
            raise OSError("replacement source ancestry changed before recovery")
        root_record = transition.get("private_root_identity")
        operation_record = transition.get("private_identity")
        if not isinstance(root_record, dict) or not isinstance(operation_record, dict):
            raise OSError("replacement private namespace identity is unavailable")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        root_fd = os.open(_PRIVATE_REPLACEMENT_ROOT, flags | nofollow, dir_fd=parent_fd)
        root_stat = os.fstat(root_fd)
        root_entry = os.stat(_PRIVATE_REPLACEMENT_ROOT, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not _StagedReplacement._same_identity(root_stat, root_entry)
            or root_stat.st_dev != os.fstat(parent_fd).st_dev
            or root_record.get("device") != root_stat.st_dev
            or root_record.get("inode") != root_stat.st_ino
            or stat.S_IMODE(root_stat.st_mode) != 0o700
            or (hasattr(os, "geteuid") and root_stat.st_uid != os.geteuid())
        ):
            raise OSError("private replacement anchor identity changed before recovery")
        operation_name = _safe_private_entry_name(transition.get("operation_name"))
        operation_fd = os.open(operation_name, flags | nofollow, dir_fd=root_fd)
        operation_stat = os.fstat(operation_fd)
        operation_entry = os.stat(operation_name, dir_fd=root_fd, follow_symlinks=False)
        if (
            not _StagedReplacement._same_identity(operation_stat, operation_entry)
            or operation_stat.st_dev != root_stat.st_dev
            or operation_record.get("device") != operation_stat.st_dev
            or operation_record.get("inode") != operation_stat.st_ino
            or stat.S_IMODE(operation_stat.st_mode) != 0o700
        ):
            raise OSError("per-operation replacement namespace identity changed before recovery")
        staged_name = _safe_private_entry_name(transition.get("staged_name"))
        holding_name = _safe_private_entry_name(transition.get("holding_name"))
        result = (parent_fd, root_fd, operation_fd, source_name, staged_name, holding_name)
        # The withdrawn entry name is returned separately by the caller from the record.
        parent_fd = root_fd = operation_fd = -1
        return result
    except BaseException:
        if operation_fd is not None:
            os.close(operation_fd)
        if root_fd is not None:
            os.close(root_fd)
        os.close(parent_fd)
        raise


def _checkpoint_replacement_recovery(
    transition: dict[str, object],
    phase: str,
    checkpoint: Callable[[dict[str, object]], None] | None,
    **extra: object,
) -> dict[str, object]:
    updated = {**transition, "phase": phase, **extra}
    if checkpoint is not None:
        checkpoint(updated)
    return updated


def recover_publication_transition(
    path: Path,
    transition: dict[str, object],
    *,
    library_root: Path | None,
    checkpoint: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Reconcile an interrupted replacement to the entry it quarantined, without clobbering."""
    parent_fd, root_fd, private_fd, source_name, _staged_name, holding_name = (
        _open_replacement_namespace(path, transition, library_root)
    )
    withdrawn_name_raw = transition.get("withdrawn_name")
    withdrawn_name = (
        _safe_private_entry_name(withdrawn_name_raw)
        if isinstance(withdrawn_name_raw, str)
        else None
    )
    source_guard = transition.get("source_guard")
    expected_source_file = source_guard.get("file") if isinstance(source_guard, dict) else None
    staged_file = transition.get("staged_file")
    expected_library = (
        str(Path(os.path.abspath(library_root))) if library_root is not None else None
    )
    if (
        not isinstance(source_guard, dict)
        or source_guard.get("version") != 1
        or source_guard.get("path") != str(Path(os.path.abspath(path)))
        or source_guard.get("library_root") != expected_library
        or source_guard.get("directories") != transition.get("parent_directories")
        or not isinstance(expected_source_file, dict)
        or not isinstance(staged_file, dict)
    ):
        for descriptor in (private_fd, root_fd, parent_fd):
            os.close(descriptor)
        raise OSError("replacement recovery lacks expected source or staged content evidence")
    try:
        source_entry = _replacement_entry_evidence(parent_fd, source_name)
        held_entry = _replacement_entry_evidence(private_fd, holding_name)
        withdrawn_entry = (
            _replacement_entry_evidence(private_fd, withdrawn_name)
            if withdrawn_name is not None
            else None
        )
        source_file = source_entry.get("file") if source_entry is not None else None
        held_file = held_entry.get("file") if held_entry is not None else None
        matches_original = source_file == expected_source_file
        matches_staged = source_file == staged_file
        recorded_restored = transition.get("restored_entry")
        if (
            transition.get("phase") == "recovered"
            and source_entry is not None
            and held_entry is None
            and isinstance(recorded_restored, dict)
            and all(
                source_entry.get(key) == recorded_restored.get(key)
                for key in ("device", "inode", "mode", "size", "mtime_ns")
            )
        ):
            return transition
        compensated_entry = transition.get("compensated_entry")
        prior_held = transition.get("held_entry")
        if (
            source_entry is not None
            and held_entry is None
            and transition.get("phase") in {"compensated", "compensation_intent"}
            and any(
                isinstance(record, dict)
                and all(
                    source_entry.get(key) == record.get(key)
                    for key in ("device", "inode", "mode", "size", "mtime_ns")
                )
                for record in (compensated_entry, prior_held)
            )
        ):
            updated = _checkpoint_replacement_recovery(
                transition,
                "recovered",
                checkpoint,
                source_restored=source_file == expected_source_file,
                restored_entry=source_entry,
            )
            return updated

        if matches_original and held_entry is None:
            updated = _checkpoint_replacement_recovery(
                transition, "recovered", checkpoint, source_restored=True
            )
            return updated

        if source_entry is None and held_entry is not None:
            _checkpoint_replacement_recovery(
                transition, "recovery_restore_intent", checkpoint, held_entry=held_entry
            )
            _rename_entry_no_replace(private_fd, holding_name, parent_fd, source_name)
            os.fsync(private_fd)
            os.fsync(parent_fd)
            restored = _replacement_entry_evidence(parent_fd, source_name)
            if restored is None or any(
                restored.get(key) != held_entry.get(key)
                for key in ("device", "inode", "mode", "size", "mtime_ns")
            ):
                raise OSError("quarantined replacement changed while restoring it")
            updated = _checkpoint_replacement_recovery(
                transition,
                "recovered",
                checkpoint,
                source_restored=restored.get("file") == expected_source_file,
                restored_entry=restored,
            )
            return updated

        if matches_staged and held_file == expected_source_file and withdrawn_name is not None:
            if withdrawn_entry is not None:
                raise OSError("replacement recovery holding slot is already occupied")
            _checkpoint_replacement_recovery(
                transition,
                "recovery_withdraw_intent",
                checkpoint,
                staged_entry=source_entry,
            )
            _rename_entry_no_replace(parent_fd, source_name, private_fd, withdrawn_name)
            os.fsync(parent_fd)
            os.fsync(private_fd)
            withdrawn_entry = _replacement_entry_evidence(private_fd, withdrawn_name)
            if withdrawn_entry is None or withdrawn_entry.get("file") != staged_file:
                raise OSError("published entry was not the staged file during recovery withdrawal")
            _checkpoint_replacement_recovery(
                transition,
                "recovery_withdrawn",
                checkpoint,
                withdrawn_entry=withdrawn_entry,
            )
            _rename_entry_no_replace(private_fd, holding_name, parent_fd, source_name)
            os.fsync(private_fd)
            os.fsync(parent_fd)
            restored = _replacement_entry_evidence(parent_fd, source_name)
            if restored is None or restored.get("file") != expected_source_file:
                raise OSError(
                    "verified original could not be restored after publication withdrawal"
                )
            updated = _checkpoint_replacement_recovery(
                transition,
                "recovered",
                checkpoint,
                source_restored=True,
                withdrawn_entry=withdrawn_entry,
            )
            return updated

        raise OSError(
            "recovery_required: replacement entries cannot be reconciled without overwriting a live entry"
        )
    finally:
        for descriptor in (private_fd, root_fd, parent_fd):
            os.close(descriptor)


def finalize_publication_transition(
    path: Path,
    transition: dict[str, object],
    *,
    library_root: Path | None,
) -> bool:
    """Remove only a verified held inverse after its completed DB outcome is durable."""
    if transition.get("phase") != "complete":
        return False
    try:
        parent_fd, root_fd, private_fd, _source_name, staged_name, holding_name = (
            _open_replacement_namespace(path, transition, library_root)
        )
    except OSError:
        return False
    try:
        expected = transition.get("source_guard")
        expected_file = expected.get("file") if isinstance(expected, dict) else None
        published = transition.get("published_guard")
        if not isinstance(expected_file, dict) or not isinstance(published, dict):
            return False
        try:
            actual_published = capture_file_guard(path, library_root)
        except OSError:
            return False
        if actual_published != published:
            return False
        held = _replacement_entry_evidence(private_fd, holding_name)
        if held is not None:
            if held.get("file") != expected_file:
                return False
            os.unlink(holding_name, dir_fd=private_fd)
        staged_expected = transition.get("staged_file")
        staged = _replacement_entry_evidence(private_fd, staged_name)
        if staged is not None:
            if staged.get("file") != staged_expected:
                return False
            os.unlink(staged_name, dir_fd=private_fd)
        os.fsync(private_fd)
        operation_name = _safe_private_entry_name(transition.get("operation_name"))
        if not os.listdir(private_fd):
            operation_entry = os.stat(operation_name, dir_fd=root_fd, follow_symlinks=False)
            if _StagedReplacement._same_identity(operation_entry, os.fstat(private_fd)):
                os.rmdir(operation_name, dir_fd=root_fd)
                os.fsync(root_fd)
        return True
    except OSError:
        return False
    finally:
        for descriptor in (private_fd, root_fd, parent_fd):
            os.close(descriptor)


def cleanup_recovered_publication_transition(
    path: Path,
    transition: dict[str, object],
    *,
    library_root: Path | None,
) -> bool:
    """Remove only operation-owned staging entries after recovery was checkpointed."""
    if transition.get("phase") != "recovered":
        return False
    try:
        parent_fd, root_fd, private_fd, _source_name, staged_name, holding_name = (
            _open_replacement_namespace(path, transition, library_root)
        )
    except OSError:
        return False
    withdrawn_raw = transition.get("withdrawn_name")
    withdrawn_name = (
        _safe_private_entry_name(withdrawn_raw) if isinstance(withdrawn_raw, str) else None
    )
    staged_expected = transition.get("staged_file")
    try:
        if _replacement_entry_evidence(private_fd, holding_name) is not None:
            return False
        for name in (staged_name, withdrawn_name):
            if name is None:
                continue
            entry = _replacement_entry_evidence(private_fd, name)
            if entry is None:
                continue
            if not isinstance(staged_expected, dict) or entry.get("file") != staged_expected:
                return False
            os.unlink(name, dir_fd=private_fd)
        os.fsync(private_fd)
        operation_name = _safe_private_entry_name(transition.get("operation_name"))
        if not os.listdir(private_fd):
            operation_entry = os.stat(operation_name, dir_fd=root_fd, follow_symlinks=False)
            if _StagedReplacement._same_identity(operation_entry, os.fstat(private_fd)):
                os.rmdir(operation_name, dir_fd=root_fd)
                os.fsync(root_fd)
        return True
    except OSError:
        return False
    finally:
        for descriptor in (private_fd, root_fd, parent_fd):
            os.close(descriptor)


def _transition_from_before_blob(before_blob: dict[str, Any]) -> dict[str, object] | None:
    transition = before_blob.get(_PUBLICATION_TRANSITION_KEY)
    return (
        {str(key): value for key, value in transition.items()}
        if isinstance(transition, dict)
        else None
    )


def _mark_publication_transition_complete(before_blob: dict[str, Any]) -> dict[str, Any]:
    transition = _transition_from_before_blob(before_blob)
    if transition is None or transition.get("phase") != "published":
        return before_blob
    return {
        **before_blob,
        _PUBLICATION_TRANSITION_KEY: {**transition, "phase": "complete"},
    }


def write_tag_fields(
    session: Session,
    *,
    apply_run_id: int,
    track: Track,
    field_values: dict[str, Any],
    art_blob_id: int | None,
    remove_art: bool,
    lyrics_payload: dict[str, object] | None,
    lyrics_remove: bool,
    blob_store: BlobStore | None,
    backup_store: BackupStore | None,
    source_precondition: SourcePrecondition | None,
    library_root: Path | None,
) -> tuple[bool, str | None]:
    """Write tags/art/lyrics for one track, journaled via ReviewFileJournal."""
    precondition_error = _source_precondition_error(
        track, source_precondition, library_root=library_root
    )
    conflict = probe(track.path, track.tag_hash) if precondition_error is None else None
    if precondition_error is not None or (conflict is not None and conflict.conflicted):
        journal = ReviewFileJournal(
            apply_run_id=apply_run_id,
            track_id=track.id,
            path=track.path,
            phase="tags",
            state="failed",
            before_hash=track.tag_hash,
            after_hash=conflict.current_tag_hash if conflict is not None else None,
            before_blob={},
            error=precondition_error
            or (conflict.error if conflict is not None else None)
            or "tag_hash mismatch",
        )
        session.add(journal)
        session.flush()
        return False, journal.error

    try:
        physical_before = capture_file_guard(Path(track.path), library_root)
    except Exception as exc:
        error = f"could not establish physical file baseline: {exc}"
        journal = ReviewFileJournal(
            apply_run_id=apply_run_id,
            track_id=track.id,
            path=track.path,
            phase="tags",
            state="failed",
            before_hash=track.tag_hash,
            before_blob={},
            error=error,
        )
        session.add(journal)
        session.flush()
        return False, error

    if backup_store is not None:
        try:
            content_hash = track.content_hash
            if content_hash is None:
                content_hash = partial_content_hash(
                    Path(track.path), Path(track.path).stat().st_size
                )
            backup_store.backup(Path(track.path), content_hash)
        except (BackupError, OSError) as exc:
            journal = ReviewFileJournal(
                apply_run_id=apply_run_id,
                track_id=track.id,
                path=track.path,
                phase="tags",
                state="failed",
                before_hash=track.tag_hash,
                before_blob={},
                error=str(exc),
            )
            session.add(journal)
            session.flush()
            return False, str(exc)

    if art_blob_id is not None and blob_store is None:
        return False, "embed_art requires blob store"
    # lyrics and art handling uses same tmp file path
    before_blob = _meta_to_field_dict(track)
    before_blob["__muzilla_physical_guard_before"] = physical_before
    if lyrics_payload is not None or lyrics_remove:
        try:
            before_blob["__muzilla_lyrics"] = read_lyrics(Path(track.path))
        except Exception:
            before_blob["__muzilla_lyrics"] = None
    if art_blob_id is not None or remove_art:
        before_blob["__muzilla_catalog_art_blob_id"] = track.art_blob_id
        try:
            artwork = capture_embedded_art(Path(track.path))
            stored_entries: list[dict[str, Any]] = []
            entries = artwork["entries"]
            if entries and blob_store is None:
                raise OSError("blob store is required to preserve embedded artwork")
            if track.art_blob_id is not None:
                if blob_store is None:
                    raise OSError("blob store is required to preserve catalog artwork identity")
                catalog_blob = blob_store.get_by_id(session, track.art_blob_id)
                if catalog_blob is None:
                    raise OSError(f"catalog artwork blob {track.art_blob_id} is unavailable")
                blob_store.get_durable_bytes(catalog_blob)
                blob_store.retain(session, catalog_blob)
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("data"), bytes):
                    raise OSError("embedded artwork capture returned an invalid entry")
                entry_data = dict(entry)
                data = entry_data.pop("data")
                mime = entry_data.get("mime")
                if not isinstance(mime, str):
                    image_format = entry_data.get("image_format")
                    mime = "image/png" if image_format == 14 else "image/jpeg"
                assert blob_store is not None
                stored_blob = blob_store.put(session, data, mime=mime)
                blob_store.retain(session, stored_blob)
                entry_data["blob_id"] = stored_blob.id
                stored_entries.append(entry_data)
            before_blob["__muzilla_embedded_art"] = {
                "version": 1,
                "format": artwork["format"],
                "entries": stored_entries,
            }
        except Exception as exc:
            journal = ReviewFileJournal(
                apply_run_id=apply_run_id,
                track_id=track.id,
                path=track.path,
                phase="tags",
                state="failed",
                before_hash=track.tag_hash,
                before_blob={},
                error=f"artwork inverse capture/storage failed: {exc}",
            )
            session.add(journal)
            session.flush()
            return False, journal.error

    has_changes = (
        bool(field_values)
        or art_blob_id is not None
        or remove_art
        or lyrics_payload is not None
        or lyrics_remove
    )
    if not has_changes:
        return True, None

    journal = ReviewFileJournal(
        apply_run_id=apply_run_id,
        track_id=track.id,
        path=track.path,
        phase="tags",
        state="pending",
        before_hash=track.tag_hash,
        before_blob=before_blob,
    )
    session.add(journal)
    journal.state = "writing"
    session.commit()
    journal_id = journal.id

    target = Path(track.path)
    staged: _StagedReplacement | None = None

    def persist_replacement_checkpoint(checkpoint: dict[str, object]) -> None:
        journal.before_blob = {
            **dict(journal.before_blob or {}),
            _PUBLICATION_TRANSITION_KEY: checkpoint,
        }
        session.commit()

    try:
        with _StagedReplacement(
            target,
            library_root,
            expected_guard=physical_before,
            checkpoint=persist_replacement_checkpoint,
            purpose="apply",
        ) as staged:
            filething = staged.filething
            if field_values:
                write_fields(filething, field_values)
            if art_blob_id is not None:
                assert blob_store is not None
                blob_tmp = blob_store.get_by_id(session, art_blob_id)
                if blob_tmp is None:
                    raise TagWriteError(target, ValueError(f"blob {art_blob_id} not found"))
                write_art(filething, blob_store.get_bytes(blob_tmp), blob_tmp.mime)
            elif remove_art:
                clear_art(filething)
            if lyrics_payload is not None:
                write_lyrics(filething, str(lyrics_payload["text"]))
            elif lyrics_remove:
                clear_lyrics(filething)
            staged.publish(library_root)
            physical_after = staged.capture_published_guard(library_root)
            journal.before_blob = {
                **dict(journal.before_blob or {}),
                "__muzilla_physical_guard_after": physical_after,
            }
            # Keep durable physical evidence while the journal remains writing.
            session.commit()
            filething.fileobj.seek(0)
            after_meta = read_track(filething)  # type: ignore[arg-type]
            after_hash = compute_tag_hash(after_meta)
            complete_transition = staged.complete_transition()
            if complete_transition is None:
                raise OSError("replacement completion evidence is unavailable")
            journal.before_blob = {
                **dict(journal.before_blob or {}),
                _PUBLICATION_TRANSITION_KEY: complete_transition,
            }
            journal.state = "done"
            journal.after_hash = after_hash
            session.flush()
            # update track cached fields
            for f, v in field_values.items():
                setattr(  # pyright: ignore[reportUnknownArgumentType]
                    track,
                    f,
                    tuple(v) if isinstance(v, list) and f in ("artists", "genre", "mood") else v,  # pyright: ignore[reportUnknownArgumentType]
                )
            _update_track_file_facts(track, target, tag_hash=after_hash, file_fd=staged.temp_fd)
        if art_blob_id is not None:
            assert blob_store is not None
            blob = blob_store.get_by_id(session, art_blob_id)
            if blob is not None:
                blob_store.retain(session, blob)
            track.art_blob_id = art_blob_id
            track.has_embedded_art = True
        elif remove_art:
            track.art_blob_id = None
            track.has_embedded_art = False
        if lyrics_payload is not None:
            track.has_lyrics = True
            track.lyrics_synced = bool(lyrics_payload.get("synced"))
        elif lyrics_remove:
            track.has_lyrics = False
            track.lyrics_synced = False
        session.commit()
        transition = _transition_from_before_blob(dict(journal.before_blob or {}))
        if transition is not None:
            finalize_publication_transition(target, transition, library_root=library_root)
        return True, None
    except Exception as exc:
        recovery_required = bool(
            staged is not None
            and (staged.transition_started or staged.published or staged.publication_uncertain)
        )
        return _persist_file_journal_failure(
            session,
            journal_id,
            str(exc),
            recovery_required=recovery_required,
        )


def write_move(
    session: Session,
    *,
    apply_run_id: int,
    track: Track,
    destination: str,
    library_root: Path | None,
    create_directories: bool,
) -> tuple[bool, str | None]:
    source = Path(track.path)
    dest = Path(destination)
    if library_root is not None:
        resolved_root = library_root.resolve()
        candidate_dest = dest if dest.is_absolute() else (resolved_root / dest)
        for parent in candidate_dest.parents:
            if parent == resolved_root:
                break
            if parent.exists() and parent.is_symlink():
                return False, f"refusing to follow symlink: {parent}"
        resolved_dest = candidate_dest.resolve()
        if resolved_root != resolved_dest and resolved_root not in resolved_dest.parents:
            return False, f"refusing to write outside library root: {dest}"
        dest = resolved_dest
    # Exact path no-op: no filesystem mutation, no journal.
    if source == dest:
        return True, None
    if not source.exists():
        return False, f"source file no longer exists: {source}"
    try:
        source_stat = source.stat()
    except OSError as exc:
        return False, f"source file stat failed: {exc}"
    if dest.exists() and not is_case_only_entry_alias(source, dest):
        return False, f"destination already exists: {dest}"
    try:
        physical_before = capture_file_guard(source, library_root)
    except Exception as exc:
        return False, f"could not establish physical move baseline: {exc}"
    journal = ReviewFileJournal(
        apply_run_id=apply_run_id,
        track_id=track.id,
        path=track.path,
        phase="move",
        state="pending",
        before_path=str(source),
        after_path=str(dest),
        before_blob={"__muzilla_physical_guard_before": physical_before},
    )
    session.add(journal)
    journal.state = "writing"
    session.commit()
    journal_id = journal.id
    moved = False
    uncertain = False
    try:
        if create_directories:
            dest.parent.mkdir(parents=True, exist_ok=True)

        def persist_case_move(checkpoint: dict[str, object]) -> None:
            journal.before_blob = {
                **dict(journal.before_blob or {}),
                "__muzilla_case_move": checkpoint,
            }
            session.commit()

        try:
            physical_after = move_file_with_guard(
                source,
                dest,
                expected_source_guard=physical_before,
                library_root=library_root,
                checkpoint=persist_case_move,
            )
            moved = True
        except FileExistsError as exc:
            raise OSError(f"destination already exists: {dest}") from exc
        journal.before_blob = {
            **dict(journal.before_blob or {}),
            "__muzilla_physical_guard_after": physical_after,
        }
        session.commit()
        journal.state = "done"
        session.flush()
        track.path = str(dest)
        track.filename = dest.name
        current_meta = read_track(dest)
        _update_track_file_facts(track, dest, tag_hash=compute_tag_hash(current_meta))
        session.commit()
        return True, None
    except Exception as exc:
        if not moved:
            try:
                source_after = os.stat(source, follow_symlinks=False)
                destination_after = os.stat(dest, follow_symlinks=False)
                source_is_original = _StagedReplacement._same_identity(source_stat, source_after)
                destination_is_original = _StagedReplacement._same_identity(
                    source_stat, destination_after
                )
                moved = destination_is_original and not source_is_original
                uncertain = not source_is_original and not destination_is_original
            except FileNotFoundError:
                try:
                    os.stat(source, follow_symlinks=False)
                except FileNotFoundError:
                    moved = True
                except OSError:
                    uncertain = True
        checkpoint_recorded = isinstance(
            (journal.before_blob or {}).get("__muzilla_case_move"), dict
        )
        return _persist_file_journal_failure(
            session,
            journal_id,
            str(exc),
            recovery_required=moved or uncertain or checkpoint_recorded,
        )


def _restore_from_before_blob(
    session: Session,
    path: Path,
    before_blob: dict[str, Any],
    *,
    blob_store: BlobStore | None,
    library_root: Path | None,
    expected_guard: dict[str, object] | None,
    checkpoint: Callable[[dict[str, object]], None] | None,
) -> dict[str, object]:
    payload = dict(before_blob)
    payload.pop("__muzilla_physical_guard_before", None)
    payload.pop("__muzilla_physical_guard_after", None)
    payload.pop("__muzilla_physical_guard_restored", None)
    lyrics = payload.pop("__muzilla_lyrics", ...)
    embedded_art = payload.pop("__muzilla_embedded_art", None)
    legacy_art_blob_id = payload.pop("__muzilla_art_blob_id", ...)
    payload.pop("__muzilla_before_art_blob_id", None)
    payload.pop("__muzilla_catalog_art_blob_id", None)
    payload.pop(_PUBLICATION_TRANSITION_KEY, None)
    with _StagedReplacement(
        path,
        library_root,
        expected_guard=expected_guard,
        checkpoint=checkpoint,
        purpose="restore",
    ) as staged:
        filething = staged.filething
        write_fields(filething, payload)
        if lyrics is not ...:
            if lyrics is None:
                clear_lyrics(filething)
            else:
                write_lyrics(filething, str(lyrics))
        if isinstance(embedded_art, dict):
            if blob_store is None and embedded_art.get("entries"):
                raise OSError("blob store is required to restore journaled artwork")
            snapshot = dict(embedded_art)
            raw_entries = snapshot.get("entries")
            if not isinstance(raw_entries, list):
                raise OSError("journal artwork entries are invalid")
            restored_entries: list[dict[str, Any]] = []
            for raw_entry in raw_entries:
                if not isinstance(raw_entry, dict):
                    raise OSError("journal artwork entry is invalid")
                try:
                    blob_id = int(raw_entry["blob_id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise OSError("journal artwork blob id is invalid") from exc
                if blob_store is None:
                    raise OSError("blob store is required to restore journaled artwork")
                blob = blob_store.get_by_id(session, blob_id)
                if blob is None:
                    raise OSError(f"journal artwork blob {blob_id} is unavailable")
                entry = dict(raw_entry)
                entry.pop("blob_id", None)
                entry["data"] = blob_store.get_bytes(blob)
                restored_entries.append(entry)
            snapshot["entries"] = restored_entries
            restore_embedded_art(filething, snapshot)
        elif legacy_art_blob_id is not ...:
            if legacy_art_blob_id is None:
                clear_art(filething)
            elif blob_store is None:
                raise OSError("blob store is required to restore journaled artwork")
            else:
                try:
                    blob_id_int = int(legacy_art_blob_id)
                except (TypeError, ValueError) as exc:
                    raise OSError(
                        f"journal artwork blob id is invalid: {legacy_art_blob_id!r}"
                    ) from exc
                blob = blob_store.get_by_id(session, blob_id_int)
                if blob is None:
                    raise OSError(f"journal artwork blob {legacy_art_blob_id} is unavailable")
                write_art(filething, blob_store.get_bytes(blob), blob.mime)
        staged.publish(library_root)
        return staged.capture_published_guard(library_root)


def restore_catalog_art_identity(
    session: Session,
    track: Track,
    before_blob: dict[str, Any],
    *,
    blob_store: BlobStore | None,
) -> None:
    """Restore catalog identity separately from the physical artwork inverse."""
    desired = before_blob.get(
        "__muzilla_catalog_art_blob_id", before_blob.get("__muzilla_art_blob_id", track.art_blob_id)
    )
    if desired is not None and (not isinstance(desired, int) or isinstance(desired, bool)):
        raise OSError("journal catalog artwork id is invalid")
    current = track.art_blob_id
    if current != desired and blob_store is not None:
        if current is not None:
            current_blob = blob_store.get_by_id(session, current)
            if current_blob is not None:
                blob_store.release(session, current_blob)
        if desired is not None:
            catalog_blob = blob_store.get_by_id(session, desired)
            if catalog_blob is None:
                raise OSError(f"catalog artwork blob {desired} is unavailable during restore")
            blob_store.get_bytes(catalog_blob)
    track.art_blob_id = desired
    snapshot = before_blob.get("__muzilla_embedded_art")
    entries = snapshot.get("entries") if isinstance(snapshot, dict) else None
    track.has_embedded_art = bool(entries) if isinstance(entries, list) else desired is not None


def restore_from_before_blob(
    session: Session,
    path: Path,
    before_blob: dict[str, Any],
    *,
    blob_store: BlobStore | None,
    library_root: Path | None = None,
    expected_guard: dict[str, object] | None = None,
    checkpoint: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Restore one journal inverse through the protected staged-file boundary."""
    return _restore_from_before_blob(
        session,
        path,
        before_blob,
        blob_store=blob_store,
        library_root=library_root,
        expected_guard=expected_guard,
        checkpoint=checkpoint,
    )
