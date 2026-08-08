"""Applies a DRAFT ChangeSet to disk (docs/PLAN.md §4) — the
highest-risk code in the project (docs/PLAN.md "Critical Files").

True cross-file ACID is impossible (files are separate FS objects).
This gives crash-**recoverable**, not crash-**atomic**, semantics via a
write-ahead journal plus per-file atomic replace:

1. Probe — re-read the file, recompute tag_hash. Differs from the
   value recorded at stage time -> conflict; abort that file's writes,
   mark its Changes `apply_state="conflicted"`. Files are truth, so
   drift is detected, never steamrolled (CLAUDE.md).
2. Journal row PENDING with `before_blob` = the complete original tag
   payload (art excluded from the JSON blob — stored in the blob store
   and referenced by id only).
3. Copy to `<target>.muzilla.tmp` in the SAME directory (same
   filesystem -> atomic rename), write tags there, fsync.
4. `os.replace(tmp, target)` — atomic on POSIX.
5. Journal -> DONE with `after_hash` and the catalog facts from that file.

There is intentionally no cross-file rollback or global atomicity claim.  Each file has
its own durable outcome; a ReviewBundle aggregates those outcomes and retries only work
that did not already succeed.
"""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.changes.backup import BackupError, BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.conflicts import probe
from muzilla.db.models import ApplyJournal, Change, ChangeSet, Track, TrackGroup
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


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    reverted: int = 0
    """Journal rows proven unchanged or successfully restored from before_blob."""
    confirmed_done: int = 0
    """Journal rows whose write demonstrably completed (on-disk hash
    matches after_hash) — marked done, nothing to restore."""
    failed: int = 0
    """Journal rows whose outcome could not be restored or proven."""


@dataclass(frozen=True, slots=True)
class ApplyResult:
    change_set_id: int
    state: str
    """applied | partially_applied | failed"""
    applied_track_ids: list[int] = dc_field(default_factory=list)
    conflicted_track_ids: list[int] = dc_field(default_factory=list)
    errors: dict[int, str] = dc_field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourcePrecondition:
    """Immutable file facts captured by the ReviewBundle SourceSnapshot."""

    path: str
    size_bytes: int
    mtime_ns: int
    tag_hash: str


def _fsync_directory(path: Path) -> None:
    """Persist a replace/rename directory entry on POSIX filesystems."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _move_no_clobber(source: Path, destination: Path, *, same_file: bool) -> None:
    """Atomically rename without replacing a destination created after preview.

    A case-only rename on a case-insensitive filesystem resolves to the same inode and
    is safe with ``os.replace``.  Linux and macOS otherwise expose an exclusive rename
    flag that closes the check-then-replace race without a two-name hard-link window.
    Unsupported platforms fail closed instead of weakening collision safety.
    """
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
        raise OSError(
            errno.ENOTSUP, "atomic no-clobber rename is unsupported"
        ) from exc
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), str(destination))


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


def _source_precondition_error(
    track: Track,
    expected: SourcePrecondition | None,
    *,
    library_root: Path | None,
) -> str | None:
    path = Path(track.path)
    # Legacy ChangeSets predate frozen SourceSnapshots. Preserve their existing
    # conflict contract; the stricter containment/stat checks belong to the
    # ReviewBundle path, where an explicit snapshot is always supplied.
    if expected is None:
        return None
    guard_error = _source_guard_error(path, library_root)
    if guard_error is not None:
        return guard_error
    if track.path != expected.path:
        return "source snapshot path no longer matches the catalog"
    try:
        stat = path.stat()
    except OSError as exc:
        return f"source snapshot stat failed: {exc}"
    if stat.st_size != expected.size_bytes or stat.st_mtime_ns != expected.mtime_ns:
        return "source snapshot stat changed after review"
    conflict = probe(str(path), expected.tag_hash)
    if conflict.conflicted:
        return conflict.error or "source snapshot tag hash changed after review"
    return None


def _meta_to_field_dict(track: Track) -> dict[str, Any]:
    """The complete tag payload for a track, in the same field-name
    space Change rows use — this is what `before_blob` snapshots."""
    from dataclasses import fields as dataclass_fields

    from muzilla.domain.metadata import TrackMeta

    payload: dict[str, Any] = {}
    for f in dataclass_fields(TrackMeta):
        if f.name in ("duration_ms", "bitrate", "sample_rate", "channels", "codec"):
            continue  # read-only probe fields, never part of a tag payload
        value = getattr(track, f.name, None)
        payload[f.name] = list(value) if isinstance(value, tuple) else value
    return payload


def _update_track_file_facts(track: Track, path: Path, *, tag_hash: str) -> None:
    stat = path.stat()
    track.size_bytes = stat.st_size
    track.mtime_ns = stat.st_mtime_ns
    track.content_hash = partial_content_hash(path, stat.st_size)
    track.tag_hash = tag_hash
    track.missing_since = None


def _apply_track_group(
    session: Session,
    change_set: ChangeSet,
    track: Track,
    track_changes: list[Change],
    *,
    library_root: Path | None,
    create_directories: bool,
    blob_store: BlobStore | None,
    backup_store: BackupStore | None,
    source_precondition: SourcePrecondition | None,
) -> tuple[bool, str | None]:
    """Applies every accepted Change for one track — tag edits and a
    rename are independent sub-steps sharing one conflict probe, each
    producing its own ApplyJournal row (phase='tags' / phase='move').
    Tags apply first so a move's os.replace picks up the already-
    updated tag bytes rather than needing a second read/write pass.
    Returns (success, error_message); success requires BOTH sub-steps
    to succeed, matching the existing all-or-nothing-per-track
    contract (a Change's own apply_state still records which specific
    sub-step failed when only one does)."""
    accepted = [c for c in track_changes if c.decision == "accepted"]
    if not accepted:
        return True, None

    precondition_error = _source_precondition_error(
        track, source_precondition, library_root=library_root
    )
    conflict = probe(track.path, track.tag_hash) if precondition_error is None else None
    if precondition_error is not None or (conflict is not None and conflict.conflicted):
        for c in accepted:
            c.apply_state = "conflicted"
        journal = ApplyJournal(
            change_set_id=change_set.id,
            track_id=track.id,
            path=track.path,
            phase="tags",
            state="failed",
            before_hash=track.tag_hash,
            after_hash=conflict.current_tag_hash if conflict is not None else None,
            before_blob={},
            error=precondition_error
            or (conflict.error if conflict is not None else None)
            or "tag_hash mismatch: file modified since staging",
        )
        session.add(journal)
        session.flush()
        return False, journal.error

    if backup_store is not None:
        try:
            content_hash = track.content_hash
            if content_hash is None:
                # content_hash is only ever null when the track's tags
                # failed to read at scan time (pipeline/scan.py sets it
                # unconditionally on every successful probe) — not
                # deferred for cost reasons. Silently skipping the
                # backup here would report the apply as successful
                # while nothing was actually copied (changes/backup.py's
                # own docstring: a silently-skipped backup is worse than
                # no backup feature at all), so recompute it fresh
                # rather than trust a stale/absent stored value. If the
                # file itself can't be read for this, that's a genuine
                # backup failure, not a reason to skip backing up.
                content_hash = partial_content_hash(
                    Path(track.path), Path(track.path).stat().st_size
                )
            backup_store.backup(Path(track.path), content_hash)
        except (BackupError, OSError) as exc:
            for c in accepted:
                c.apply_state = "failed"
            journal = ApplyJournal(
                change_set_id=change_set.id,
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

    move_change = next((c for c in accepted if c.op == "move"), None)
    art_change = next((c for c in accepted if c.op == "embed_art"), None)
    lyrics_change = next((c for c in accepted if c.op == "write_lyrics"), None)
    field_values: dict[str, Any] = {
        c.field: _from_jsonable(c.new_value)
        for c in accepted
        if c.op not in ("move", "embed_art", "write_lyrics")
    }

    if art_change is not None and blob_store is None:
        art_change.apply_state = "failed"
        return False, "embed_art change staged but no blob store configured"

    tags_ok, tags_error = True, None
    if field_values or art_change is not None or lyrics_change is not None:
        tags_ok, tags_error = _apply_tag_fields(
            session, change_set, track, accepted, field_values, art_change, blob_store, lyrics_change
        )

    move_ok, move_error = True, None
    if move_change is not None and tags_ok:
        move_ok, move_error = _apply_move(
            session,
            change_set,
            track,
            move_change,
            library_root=library_root,
            create_directories=create_directories,
        )
    elif move_change is not None:
        # Tags failed — don't attempt the move against a track whose
        # on-disk tag state is now uncertain relative to what was staged.
        move_change.apply_state = "failed"
        move_ok, move_error = False, "skipped: tag write failed for this track"

    ok = tags_ok and move_ok
    error = tags_error or move_error
    return ok, error


def _apply_tag_fields(
    session: Session,
    change_set: ChangeSet,
    track: Track,
    accepted: list[Change],
    field_values: dict[str, Any],
    art_change: Change | None,
    blob_store: BlobStore | None,
    lyrics_change: Change | None,
) -> tuple[bool, str | None]:
    before_blob = _meta_to_field_dict(track)
    if lyrics_change is not None:
        before_blob["__muzilla_lyrics"] = read_lyrics(Path(track.path))
    if art_change is not None:
        before_blob["__muzilla_art_blob_id"] = track.art_blob_id
    journal = ApplyJournal(
        change_set_id=change_set.id,
        track_id=track.id,
        path=track.path,
        phase="art" if art_change is not None and not field_values and lyrics_change is None else "tags",
        state="pending",
        before_hash=track.tag_hash,
        before_blob=before_blob,
    )
    session.add(journal)
    journal.state = "writing"
    session.commit()

    target = Path(track.path)
    tmp_path = target.with_name(target.name + ".muzilla.tmp")
    tag_changes = [c for c in accepted if c.op not in ("move", "embed_art", "write_lyrics")]
    try:
        # Same-directory tmp copy so the final rename is same-filesystem
        # (required for os.replace to be atomic on POSIX).
        shutil.copy2(target, tmp_path)
        if field_values:
            write_fields(tmp_path, field_values)
        if art_change is not None:
            assert blob_store is not None  # guarded by the caller
            if art_change.new_blob_id is None:
                clear_art(tmp_path)
            else:
                new_blob = blob_store.get_by_id(session, art_change.new_blob_id)
                if new_blob is None:
                    raise TagWriteError(
                        target, ValueError(f"blob {art_change.new_blob_id} not found")
                    )
                write_art(tmp_path, blob_store.get_bytes(new_blob), new_blob.mime)
        if lyrics_change is not None:
            lyrics_payload = lyrics_change.new_value
            if lyrics_payload is None:
                clear_lyrics(tmp_path)
            else:
                assert isinstance(lyrics_payload, dict)
                write_lyrics(tmp_path, str(lyrics_payload["text"]))
        with tmp_path.open("rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
        _fsync_directory(target.parent)

        after_meta = read_track(target)
        after_hash = compute_tag_hash(after_meta)

        journal.state = "done"
        journal.after_hash = after_hash
        session.flush()

        for c in tag_changes:
            c.apply_state = "applied"

        # Refresh the Track row's cached fields so subsequent probes in
        # the same apply run (and immediate UI reads) see the new state
        # without requiring a rescan.
        for f, v in field_values.items():
            setattr(track, f, tuple(v) if isinstance(v, list) and f in ("artists", "genre", "mood") else v)
        _update_track_file_facts(track, target, tag_hash=after_hash)

        if art_change is not None:
            assert blob_store is not None
            _rebalance_art_refcounts(session, blob_store, track, art_change)
            art_change.apply_state = "applied"

        if lyrics_change is not None:
            payload = lyrics_change.new_value
            track.has_lyrics = payload is not None
            track.lyrics_synced = bool(isinstance(payload, dict) and payload.get("synced"))
            lyrics_change.apply_state = "applied"

        session.commit()
        return True, None

    except (TagReadError, TagWriteError, OSError) as exc:
        tmp_path.unlink(missing_ok=True)
        journal.state = "failed"
        journal.error = str(exc)
        session.commit()
        if art_change is not None:
            art_change.apply_state = "failed"
        if lyrics_change is not None:
            lyrics_change.apply_state = "failed"
        for c in tag_changes:
            c.apply_state = "failed"
        return False, str(exc)


def _rebalance_art_refcounts(
    session: Session, blob_store: BlobStore, track: Track, art_change: Change
) -> None:
    """Retains the newly-embedded blob (if any) and releases the track's
    previous one — refcounting so a cover shared across an album's
    tracks (docs/PLAN.md §5) isn't deleted while a sibling track still
    references it. Order matters: retain-then-release, so a blob that
    happens to be both old and new (re-embedding the same art) never
    transiently drops to zero and gets deleted out from under itself."""
    old_blob_id = track.art_blob_id
    new_blob_id = art_change.new_blob_id

    if new_blob_id is not None:
        new_blob = blob_store.get_by_id(session, new_blob_id)
        if new_blob is not None:
            blob_store.retain(session, new_blob)

    if old_blob_id is not None and old_blob_id != new_blob_id:
        old_blob = blob_store.get_by_id(session, old_blob_id)
        if old_blob is not None:
            blob_store.release(session, old_blob)

    track.art_blob_id = new_blob_id
    track.has_embedded_art = new_blob_id is not None


def _apply_move(
    session: Session,
    change_set: ChangeSet,
    track: Track,
    move_change: Change,
    *,
    library_root: Path | None,
    create_directories: bool,
) -> tuple[bool, str | None]:
    """Applies one op='move' Change: guardrails, optional mkdir -p,
    atomic os.replace (same filesystem — source and destination are
    both under one configured library root), ApplyJournal(phase='move'),
    Track.path/filename update."""
    source = Path(track.path)
    dest_str = _from_jsonable(move_change.new_value)
    if not isinstance(dest_str, str):
        move_change.apply_state = "failed"
        return False, f"invalid move destination: {dest_str!r}"
    dest = Path(dest_str)

    if library_root is not None:
        resolved_root = library_root.resolve()
        candidate_dest = dest if dest.is_absolute() else (resolved_root / dest)
        # Inspect the lexical path before resolve(), otherwise resolve would erase the
        # evidence that an existing parent was a symlink.
        for parent in candidate_dest.parents:
            if parent == resolved_root:
                break
            if parent.exists() and parent.is_symlink():
                move_change.apply_state = "failed"
                return False, f"refusing to follow symlink: {parent}"
        resolved_dest = candidate_dest.resolve()
        if resolved_root != resolved_dest and resolved_root not in resolved_dest.parents:
            move_change.apply_state = "failed"
            return False, f"refusing to write outside library root: {dest}"
        dest = resolved_dest

    if not source.exists():
        move_change.apply_state = "failed"
        return False, f"source file no longer exists: {source}"

    same_file = False
    if dest.exists():
        try:
            same_file = source.samefile(dest)
        except OSError:
            same_file = False
        if not same_file:
            move_change.apply_state = "failed"
            return False, f"destination already exists: {dest}"

    journal = ApplyJournal(
        change_set_id=change_set.id,
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
        move_change.apply_state = "applied"
        session.commit()
        return True, None

    except OSError as exc:
        journal.state = "failed"
        journal.error = str(exc)
        session.commit()
        move_change.apply_state = "failed"
        return False, str(exc)


def _prune_empty_dirs(touched_dirs: set[Path], *, library_root: Path) -> list[Path]:
    """Repeatedly rmdir any directory in `touched_dirs` (and then its
    parent, and so on) that is now empty and strictly inside
    library_root, stopping at the first non-empty ancestor or at
    library_root itself. Never removes library_root. Returns every
    directory actually removed."""
    resolved_root = library_root.resolve()
    removed: list[Path] = []
    for start in touched_dirs:
        current = start.resolve()
        while current != resolved_root and resolved_root in current.parents:
            try:
                if any(current.iterdir()):
                    break
                current.rmdir()
                removed.append(current)
            except OSError:
                break
            current = current.parent
    return removed


def _from_jsonable(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(value)
    return value


def _apply_group_changes(
    session: Session, change_set: ChangeSet, group_changes: list[Change]
) -> tuple[list[int], dict[int, str]]:
    """Grouping-correction Changes (entity_type='group') never touch
    files — they only move Track.group_id / TrackGroup fields. Always
    succeeds barring a genuinely missing row, so there is no conflict
    probe here (nothing on disk to drift)."""
    applied: list[int] = []
    errors: dict[int, str] = {}
    by_group: dict[int, list[Change]] = defaultdict(list)
    for c in group_changes:
        by_group[c.entity_id].append(c)

    # Groups whose track_count needs recomputing once every reassignment
    # in this changeset has been applied — both the destination group
    # (track_ids_add) and any *other* group a track moved away from
    # (track_ids_add reassigning a track that belonged elsewhere,
    # track_ids_remove's own group). changes/applier.py's Track.group_id
    # writes below were the only mutation happening here before Phase 7
    # item 6 made grouping_correction changesets actually apply — group.
    # track_count staleness was invisible until then, since nothing
    # applied these Change rows before.
    dirty_group_ids: set[int] = set()

    for group_id, changes in by_group.items():
        group = session.get(TrackGroup, group_id)
        if group is None:
            for c in changes:
                if c.decision == "accepted":
                    c.apply_state = "failed"
            errors[group_id] = f"group {group_id} not found"
            continue
        for c in changes:
            if c.decision != "accepted":
                continue
            new_value = _from_jsonable(c.new_value)
            if c.field == "track_ids_add":
                # pseudo-field: reassign a track into this group
                for track_id in new_value or []:
                    track = session.get(Track, track_id)
                    if track is not None:
                        if track.group_id is not None and track.group_id != group_id:
                            dirty_group_ids.add(track.group_id)
                        track.group_id = group_id
                        dirty_group_ids.add(group_id)
            elif c.field == "track_ids_remove":
                for track_id in new_value or []:
                    track = session.get(Track, track_id)
                    if track is not None and track.group_id == group_id:
                        track.group_id = None
                        dirty_group_ids.add(group_id)
            else:
                setattr(group, c.field, new_value)
            c.apply_state = "applied"
        group.is_pinned = True
        session.flush()
        applied.append(group_id)

    # Recompute track_count for every group touched above — a plain
    # COUNT rather than trusting incremental +1/-1 bookkeeping, since a
    # track can appear in more than one change within the same
    # changeset (e.g. force-to-singleton's add is the only change, but
    # a future multi-step correction could touch a group's membership
    # more than once) and a fresh count is cheap at this scale (one
    # group's membership, not the whole library).
    for group_id in dirty_group_ids:
        group = session.get(TrackGroup, group_id)
        if group is not None:
            group.track_count = (
                session.scalar(
                    select(func.count()).select_from(Track).where(Track.group_id == group_id)
                )
                or 0
            )
    if dirty_group_ids:
        session.flush()

    return applied, errors


def _update_group_art_current(
    session: Session, change_set: ChangeSet, changes: list[Change]
) -> None:
    """Publish group art only after every staged art operation succeeded.

    ``TrackGroup.art_blob_id`` is a current-state summary.  A pending, rejected,
    conflicted, or partially applied proposal must not overwrite it.  Undo carries the
    same group scope and swapped blob ids, so the identical rule restores the old value.
    """
    if change_set.scope_type != "group" or change_set.scope_id is None:
        return
    if any(
        change.decision == "accepted" and change.apply_state != "applied"
        for change in changes
    ):
        return
    art_changes = [change for change in changes if change.op == "embed_art"]
    if not art_changes or any(
        change.decision != "accepted" or change.apply_state != "applied"
        for change in art_changes
    ):
        return
    new_blob_ids = {change.new_blob_id for change in art_changes}
    if len(new_blob_ids) != 1:
        return
    group = session.get(TrackGroup, change_set.scope_id)
    if group is not None:
        group.art_blob_id = new_blob_ids.pop()
        session.flush()


def apply_changeset(
    session: Session,
    change_set_id: int,
    *,
    library_root: Path | None = None,
    create_directories: bool = False,
    blob_store: BlobStore | None = None,
    backup_store: BackupStore | None = None,
    source_preconditions: dict[int, SourcePrecondition] | None = None,
) -> ApplyResult:
    """Applies every `accepted` Change in the given DRAFT ChangeSet.

    Only `decision="accepted"` rows are written; `pending`/`rejected`
    rows are left untouched (still visible for later re-decision on a
    still-DRAFT changeset, but a changeset that has already transitioned
    past DRAFT cannot be re-applied — call undo() for a new inverse
    changeset instead).

    `library_root`/`create_directories` are needed only for `op="move"`
    Changes (rename ChangeSets) — passed explicitly by the caller
    (CLAUDE.md: pass Config values explicitly, not a global load_config()
    reach-in) rather than this module loading config itself. Omitted
    (None/False) for changesets with no move Changes. `blob_store` is
    needed only for `op="embed_art"` Changes, same explicit-parameter
    reasoning; a changeset with an embed_art Change and no blob_store
    fails that track with a clear error rather than silently skipping it.

    `backup_store` (docs/PLAN.md §11b) is optional and orthogonal to the
    Change kinds above — when given, each track's original file is
    copied there before that track's first write, deduped by
    `content_hash` so re-applying (or applying a second changeset
    against the same file) never re-copies. `None` disables backups
    entirely regardless of config, same explicit-parameter reasoning as
    the other two.
    """
    change_set = session.get(ChangeSet, change_set_id)
    if change_set is None:
        raise ValueError(f"changeset {change_set_id} not found")
    if change_set.state != "draft":
        raise ValueError(
            f"changeset {change_set_id} is in state {change_set.state!r}, expected 'draft'"
        )

    changes = list(
        session.scalars(select(Change).where(Change.change_set_id == change_set_id).order_by(Change.seq))
    )
    if not any(change.decision == "accepted" for change in changes):
        raise ValueError(f"changeset {change_set_id} has no accepted changes")

    change_set.state = "applying"
    session.flush()

    track_changes = [c for c in changes if c.entity_type == "track"]
    group_changes = [c for c in changes if c.entity_type == "group"]

    applied_track_ids: list[int] = []
    conflicted_track_ids: list[int] = []
    errors: dict[int, str] = {}
    touched_source_dirs: set[Path] = set()

    by_track: dict[int, list[Change]] = defaultdict(list)
    for c in track_changes:
        by_track[c.entity_id].append(c)

    for track_id, tc in by_track.items():
        track = session.get(Track, track_id)
        if track is None:
            for c in tc:
                if c.decision == "accepted":
                    c.apply_state = "failed"
            errors[track_id] = f"track {track_id} not found"
            continue
        move_change = next((c for c in tc if c.op == "move" and c.decision == "accepted"), None)
        source_dir_before_move = Path(track.path).parent if move_change is not None else None
        ok, error = _apply_track_group(
            session,
            change_set,
            track,
            tc,
            library_root=library_root,
            create_directories=create_directories,
            blob_store=blob_store,
            backup_store=backup_store,
            source_precondition=(source_preconditions or {}).get(track_id),
        )
        if ok:
            if any(c.decision == "accepted" for c in tc):
                applied_track_ids.append(track_id)
            if source_dir_before_move is not None and move_change is not None and move_change.apply_state == "applied":
                touched_source_dirs.add(source_dir_before_move)
        else:
            conflicted_track_ids.append(track_id)
            if error:
                errors[track_id] = error

    if create_directories and library_root is not None and touched_source_dirs:
        _prune_empty_dirs(touched_source_dirs, library_root=library_root)

    _applied_group_ids, group_errors = _apply_group_changes(session, change_set, group_changes)
    errors.update(group_errors)
    _update_group_art_current(session, change_set, changes)

    total_accepted = sum(1 for c in changes if c.decision == "accepted")
    total_failed_or_conflicted = sum(
        1 for c in changes if c.decision == "accepted" and c.apply_state in ("failed", "conflicted")
    )

    if total_failed_or_conflicted == 0:
        final_state = "applied"
    elif total_failed_or_conflicted == total_accepted:
        final_state = "failed"
    else:
        final_state = "partially_applied"

    change_set.state = final_state
    change_set.stats = {
        "total": len(changes),
        "accepted": total_accepted,
        "rejected": sum(1 for c in changes if c.decision == "rejected"),
        "pending": sum(1 for c in changes if c.decision == "pending"),
        "applied": sum(1 for c in changes if c.apply_state == "applied"),
        "failed": sum(1 for c in changes if c.apply_state in ("failed", "conflicted")),
    }
    if errors:
        change_set.error = "; ".join(f"track {k}: {v}" for k, v in errors.items())
    session.flush()

    return ApplyResult(
        change_set_id=change_set_id,
        state=final_state,
        applied_track_ids=applied_track_ids,
        conflicted_track_ids=conflicted_track_ids,
        errors=errors,
    )


def _restore_from_before_blob(
    session: Session,
    path: Path,
    before_blob: dict[str, Any],
    *,
    blob_store: BlobStore | None,
) -> None:
    """Writes `before_blob`'s tag payload back to `path` via the same
    same-directory-tmp + os.replace pattern `_apply_track_group` uses,
    so a restore is exactly as crash-recoverable as a forward write."""
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


def _mark_changeset_failed(session: Session, change_set_id: int, message: str) -> None:
    change_set = session.get(ChangeSet, change_set_id)
    if change_set is not None:
        change_set.state = "failed"
        change_set.error = message


def _update_track_from_recovered_file(session: Session, journal: ApplyJournal) -> None:
    track = session.get(Track, journal.track_id)
    if track is None:
        return
    meta = read_track(Path(journal.path))
    for field, value in _meta_to_field_dict(track).items():
        if field in {"lyrics_synced"}:
            continue
        recovered = getattr(meta, field, value)
        setattr(track, field, recovered)
    _update_track_file_facts(track, Path(journal.path), tag_hash=compute_tag_hash(meta))


def _mark_recovered_tag_changes(session: Session, journal: ApplyJournal) -> None:
    changes = list(
        session.scalars(
            select(Change).where(
                Change.change_set_id == journal.change_set_id,
                Change.entity_type == "track",
                Change.entity_id == journal.track_id,
            )
        )
    )
    for change in changes:
        if change.op != "move" and change.decision == "accepted":
            change.apply_state = "applied"


def _recover_tags_journal(
    session: Session, journal: ApplyJournal, *, blob_store: BlobStore | None
) -> str:
    """Returns 'reverted', 'done', or 'failed'. See recover_apply_journal's
    docstring for the three-way tag_hash comparison this implements."""
    path = Path(journal.path)
    path.with_name(path.name + ".muzilla.tmp").unlink(missing_ok=True)
    try:
        current_meta = read_track(path)
        current_hash: str | None = compute_tag_hash(current_meta)
    except TagReadError:
        current_hash = None

    if current_hash is not None and current_hash == journal.before_hash:
        journal.state = "reverted"
        _update_track_from_recovered_file(session, journal)
        return "reverted"
    if (
        current_hash is not None
        and journal.after_hash is not None
        and current_hash == journal.after_hash
    ):
        journal.state = "done"
        _update_track_from_recovered_file(session, journal)
        _mark_recovered_tag_changes(session, journal)
        return "done"

    try:
        _restore_from_before_blob(session, path, journal.before_blob, blob_store=blob_store)
        _update_track_from_recovered_file(session, journal)
    except (TagReadError, TagWriteError, OSError) as exc:
        journal.error = f"recovery restore failed: {exc}"
        journal.state = "failed"
        _mark_changeset_failed(
            session,
            journal.change_set_id,
            "recovery could not restore original tags; manual inspection required",
        )
        return "failed"
    journal.state = "reverted"
    journal.error = RECOVERY_RESTORED_MESSAGE
    _mark_changeset_failed(
        session,
        journal.change_set_id,
        "recovered from a crash mid-apply; restored original tags — "
        "please re-review and re-stage",
    )
    return "reverted"


def _recover_move_journal(session: Session, journal: ApplyJournal) -> str:
    """Returns 'reverted', 'done', or 'failed'. A move has no byte payload to
    restore (unlike tags' before_blob) — the file already IS its own
    content wherever it ended up; recovery here is purely about which
    of before_path/after_path reflects reality, using existence rather
    than a hash comparison (there's nothing to hash-compare for "did
    this move happen")."""
    before_path = Path(journal.before_path) if journal.before_path else None
    after_path = Path(journal.after_path) if journal.after_path else None
    before_exists = before_path is not None and before_path.exists()
    after_exists = after_path is not None and after_path.exists()

    if before_exists and not after_exists:
        # The move never happened (or was already reverted) — the file
        # is exactly where it started. Nothing to do.
        journal.state = "reverted"
        return "reverted"

    if after_exists and not before_exists:
        # The move completed before the crash; only the journal/
        # changeset bookkeeping after it was interrupted.
        journal.state = "done"
        track = session.get(Track, journal.track_id)
        if track is not None and after_path is not None:
            track.path = str(after_path)
            track.filename = after_path.name
            track.missing_since = None
        for change in session.scalars(
            select(Change).where(
                Change.change_set_id == journal.change_set_id,
                Change.entity_type == "track",
                Change.entity_id == journal.track_id,
                Change.op == "move",
                Change.decision == "accepted",
            )
        ):
            change.apply_state = "applied"
        return "done"

    # Neither exists, or both paths name different files: recovery cannot prove which
    # content belongs to the move. Do not guess or claim a revert — flag for review.
    reason = (
        "neither before_path nor after_path exists on recovery"
        if not before_exists and not after_exists
        else "both before_path and after_path exist on recovery"
    )
    journal.state = "failed"
    journal.error = reason
    _mark_changeset_failed(
        session,
        journal.change_set_id,
        f"recovered from a crash mid-apply (move phase): {reason} — "
        "please re-review and re-stage",
    )
    return "failed"


def recover_apply_journal(
    session: Session, *, blob_store: BlobStore | None = None
) -> RecoveryReport:
    """Startup-only: reconciles any `ApplyJournal` row left `pending` or
    `writing` by a worker process that crashed mid-apply.

    `_apply_track_group` already detects conflicts *during* a fresh
    apply (see `probe` above), but has no way to notice a write that
    was interrupted by a crash rather than by drift — that's what this
    closes (docs/PLAN.md: "Startup crash recovery for both jobs and
    the apply journal").

    Dispatches per `journal.phase`:

    tags — compare the file's current on-disk tag_hash against the
    journal's recorded before_hash/after_hash:
    - matches before_hash: the write never landed (or the crash was
      before any byte changed) -> mark 'reverted', nothing to restore.
    - matches after_hash: the write demonstrably completed; only the
      journal/changeset bookkeeping after it was interrupted -> mark
      'done', leave the file alone.
    - matches neither (indeterminate — e.g. the crash landed between
      the write and updating after_hash, or a concurrent external edit
      happened in the same window): restore from before_blob, mark
      'reverted', and mark the owning ChangeSet 'failed' so a human
      knows this changeset needs re-review rather than silently
      trusting whatever ended up on disk.

    move — a move has no tag content to hash-compare; use path
    existence instead. before_path exists / after_path doesn't -> the
    move never happened, 'reverted', nothing to do. after_path exists /
    before_path doesn't -> the move completed before the crash, 'done'. Either both or
    neither existing is indeterminate -> 'failed' with no false claim that a revert
    happened, and the owning ChangeSet is marked 'failed' for human re-review.
    """
    stmt = select(ApplyJournal).where(ApplyJournal.state.in_(("pending", "writing")))
    rows = list(session.scalars(stmt))

    reverted = 0
    confirmed_done = 0
    failed = 0

    for journal in rows:
        if journal.phase == "move":
            outcome = _recover_move_journal(session, journal)
        else:
            outcome = _recover_tags_journal(session, journal, blob_store=blob_store)

        if outcome == "done":
            confirmed_done += 1
        elif outcome == "reverted":
            reverted += 1
        else:
            failed += 1

    for change_set_id in {journal.change_set_id for journal in rows}:
        change_set = session.get(ChangeSet, change_set_id)
        if change_set is None:
            continue
        accepted = [change for change in change_set.changes if change.decision == "accepted"]
        applied = sum(change.apply_state == "applied" for change in accepted)
        if accepted and applied == len(accepted):
            change_set.state = "applied"
        elif applied:
            change_set.state = "partially_applied"

    if rows:
        session.commit()
    return RecoveryReport(reverted=reverted, confirmed_done=confirmed_done, failed=failed)
