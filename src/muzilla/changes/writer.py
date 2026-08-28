"""Native file writer for ReviewBundle operations.

This module owns the per-file safe mutation primitives (tmp-copy, tag write,
art/lyrics handling, fsync, atomic replace) without any ChangeSet coupling.
Journaling is via ReviewFileJournal tied to ApplyRun.

# ponytail: global file-system journal using ReviewFileJournal; per-file
# durability via tmp+fsync+replace. Upgrade path: add cross-file ordering
# or per-account locks if throughput matters.
"""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from muzilla.changes.backup import BackupError, BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.conflicts import probe
from muzilla.db.models import ReviewFileJournal, Track
from muzilla.domain.metadata import tag_hash as compute_tag_hash
from muzilla.tags.hashing import partial_content_hash
from muzilla.tags.reader import TagReadError, read_lyrics, read_track
from muzilla.tags.writer import (
    TagWriteError,
    clear_art,
    clear_lyrics,
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


def _move_no_clobber(source: Path, destination: Path, *, same_file: bool) -> None:
    if same_file:
        os.replace(source, destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
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
            result = rename(-100, source_bytes, -100, destination_bytes, 1)
        elif sys.platform == "darwin":
            rename = libc.renamex_np
            rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
            rename.restype = ctypes.c_int
            result = rename(source_bytes, destination_bytes, 0x00000004)
        else:
            raise AttributeError
    except AttributeError as exc:
        raise OSError(errno.ENOTSUP, "atomic no-clobber rename is unsupported") from exc
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), str(destination))


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


def _update_track_file_facts(track: Track, path: Path, *, tag_hash: str) -> None:
    stat = path.stat()
    track.size_bytes = stat.st_size
    track.mtime_ns = stat.st_mtime_ns
    track.content_hash = partial_content_hash(path, stat.st_size)
    track.tag_hash = tag_hash
    track.missing_since = None


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
        stat = path.stat()
    except OSError as exc:
        return f"source snapshot stat failed: {exc}"
    # tag_hash is authoritative; after rollback mtime/size may differ but hash matches
    conflict = probe(str(path), expected.tag_hash)
    if not conflict.conflicted:
        return None
    # hash drift - report stat drift if also present, otherwise hash error
    if stat.st_size != expected.size_bytes or stat.st_mtime_ns != expected.mtime_ns:
        return "source snapshot stat changed after review"
    return conflict.error or "source snapshot tag hash changed after review"


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
    if lyrics_payload is not None or lyrics_remove:
        try:
            before_blob["__muzilla_lyrics"] = read_lyrics(Path(track.path))
        except Exception:
            before_blob["__muzilla_lyrics"] = None
    if art_blob_id is not None or remove_art:
        before_blob["__muzilla_art_blob_id"] = track.art_blob_id

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

    target = Path(track.path)
    tmp_path = target.with_name(target.name + ".muzilla.tmp")
    try:
        shutil.copy2(target, tmp_path)
        if field_values:
            write_fields(tmp_path, field_values)
        if art_blob_id is not None:
            assert blob_store is not None
            blob_tmp = blob_store.get_by_id(session, art_blob_id)
            if blob_tmp is None:
                raise TagWriteError(target, ValueError(f"blob {art_blob_id} not found"))
            write_art(tmp_path, blob_store.get_bytes(blob_tmp), blob_tmp.mime)
        elif remove_art:
            clear_art(tmp_path)
        if lyrics_payload is not None:
            write_lyrics(tmp_path, str(lyrics_payload["text"]))
        elif lyrics_remove:
            clear_lyrics(tmp_path)
        with tmp_path.open("rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
        _fsync_directory(target.parent)
        after_meta = read_track(target)
        after_hash = compute_tag_hash(after_meta)
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
        _update_track_file_facts(track, target, tag_hash=after_hash)
        if art_blob_id is not None:
            assert blob_store is not None
            # refcount handling
            old_blob_id = track.art_blob_id
            blob = blob_store.get_by_id(session, art_blob_id)
            if blob is not None:
                blob_store.retain(session, blob)
            if old_blob_id is not None and old_blob_id != art_blob_id:
                old_blob = blob_store.get_by_id(session, old_blob_id)
                if old_blob is not None:
                    blob_store.release(session, old_blob)
            track.art_blob_id = art_blob_id
            track.has_embedded_art = True
        elif remove_art:
            # blobstore optional for remove
            old_blob_id = track.art_blob_id
            if old_blob_id is not None and blob_store is not None:
                old_blob = blob_store.get_by_id(session, old_blob_id)
                if old_blob is not None:
                    blob_store.release(session, old_blob)
            track.art_blob_id = None
            track.has_embedded_art = False
        if lyrics_payload is not None:
            track.has_lyrics = True
            track.lyrics_synced = bool(lyrics_payload.get("synced"))
        elif lyrics_remove:
            track.has_lyrics = False
            track.lyrics_synced = False
        session.commit()
        return True, None
    except (TagReadError, TagWriteError, OSError) as exc:
        tmp_path.unlink(missing_ok=True)
        journal.state = "failed"
        journal.error = str(exc)
        session.commit()
        return False, str(exc)


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
    if not source.exists():
        return False, f"source file no longer exists: {source}"
    same_file = False
    if dest.exists():
        try:
            same_file = source.samefile(dest)
        except OSError:
            same_file = False
        if not same_file:
            return False, f"destination already exists: {dest}"
    journal = ReviewFileJournal(
        apply_run_id=apply_run_id,
        track_id=track.id,
        path=track.path,
        phase="move",
        state="pending",
        before_path=str(source),
        after_path=str(dest),
    )
    session.add(journal)
    journal.state = "writing"
    session.commit()
    try:
        if create_directories:
            dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            _move_no_clobber(source, dest, same_file=same_file)
        except FileExistsError as exc:
            raise OSError(f"destination already exists: {dest}") from exc
        _fsync_directory(dest.parent)
        if source.parent != dest.parent:
            _fsync_directory(source.parent)
        journal.state = "done"
        session.flush()
        track.path = str(dest)
        track.filename = dest.name
        current_meta = read_track(dest)
        _update_track_file_facts(track, dest, tag_hash=compute_tag_hash(current_meta))
        session.commit()
        return True, None
    except OSError as exc:
        journal.state = "failed"
        journal.error = str(exc)
        session.commit()
        return False, str(exc)


def _restore_from_before_blob(
    session: Session,
    path: Path,
    before_blob: dict[str, Any],
    *,
    blob_store: BlobStore | None,
) -> None:
    payload = dict(before_blob)
    lyrics = payload.pop("__muzilla_lyrics", ...)
    art_blob_id = payload.pop("__muzilla_art_blob_id", ...)
    tmp_path = path.with_name(path.name + ".muzilla.tmp")
    tmp_path.write_bytes(path.read_bytes())
    write_fields(tmp_path, payload)
    if lyrics is not ...:
        if lyrics is None:
            clear_lyrics(tmp_path)
        else:
            write_lyrics(tmp_path, str(lyrics))
    if art_blob_id is not ...:
        if art_blob_id is None:
            clear_art(tmp_path)
        elif blob_store is None:
            raise OSError("blob store is required to restore journaled artwork")
        else:
            blob = blob_store.get_by_id(session, int(art_blob_id))
            if blob is None:
                raise OSError(f"journal artwork blob {art_blob_id} is unavailable")
            write_art(tmp_path, blob_store.get_bytes(blob), blob.mime)
    with tmp_path.open("rb") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)
    _fsync_directory(path.parent)


def restore_from_before_blob(
    session: Session,
    path: Path,
    before_blob: dict[str, Any],
    *,
    blob_store: BlobStore | None,
) -> None:
    """Public wrapper for journal rollback."""
    return _restore_from_before_blob(session, path, before_blob, blob_store=blob_store)
