"""Controlled per-file execution of a frozen ReviewBundle ApplyRun.

# ponytail: native ReviewBundle apply via writer primitives; per-file journal
# is ReviewFileJournal. Upgrade path: add cross-file ordering if needed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from hashlib import blake2b
from pathlib import Path
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from muzilla.changes.backup import BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.writer import SourcePrecondition, write_move, write_tag_fields
from muzilla.db.models import (
    ApplyRun,
    Operation,
    OperationAttempt,
    ReviewBundle,
    ReviewInboxEntry,
    Track,
    TrackGroup,
)
from muzilla.domain.reviews import OperationKind


class BundleApplyError(ValueError):
    pass

def _sync_inbox_state(session: Session, bundle: ReviewBundle) -> None:
    session.execute(
        update(ReviewInboxEntry)
        .where(ReviewInboxEntry.review_bundle_id == bundle.id)
        .values(
            state=bundle.state,
            issue_kind="review" if bundle.error else None,
            issue_message=bundle.error,
        )
    )

@dataclass(frozen=True, slots=True)
class FileApplyResult:
    track_id: int
    state: str
    applied_operation_ids: tuple[int, ...] = ()
    error: str | None = None

@dataclass(frozen=True, slots=True)
class BundleApplyResult:
    apply_run_id: int
    review_bundle_id: int
    state: str
    files: tuple[FileApplyResult, ...] = ()
    errors: dict[int, str] = dc_field(default_factory=dict)
    cancelled: bool = False

def _manifest_files(run: ApplyRun) -> list[dict[str, object]]:
    raw_files = run.manifest.get("files")
    if not isinstance(raw_files, list):
        raise BundleApplyError("apply manifest has no per-file entries")
    files: list[dict[str, object]] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or not isinstance(raw_file.get("track_id"), int):
            raise BundleApplyError("apply manifest contains an invalid file entry")
        files.append(cast(dict[str, object], raw_file))
    return files

def _source_precondition(raw: object) -> SourcePrecondition:
    if not isinstance(raw, dict):
        raise BundleApplyError("source snapshot is missing for file")
    path = raw.get("path")
    size_bytes = raw.get("size_bytes")
    mtime_ns = raw.get("mtime_ns")
    tag_hash = raw.get("tag_hash")
    if not isinstance(path, str) or not isinstance(size_bytes, int) or not isinstance(mtime_ns, int) or not isinstance(tag_hash, str):
        raise BundleApplyError("source snapshot is incomplete for file")
    return SourcePrecondition(path=path, size_bytes=size_bytes, mtime_ns=mtime_ns, tag_hash=tag_hash)

def _current_precondition(track: Track) -> SourcePrecondition:
    if track.tag_hash is None:
        raise BundleApplyError("catalog has no current tag hash for retry")
    return SourcePrecondition(path=track.path, size_bytes=track.size_bytes, mtime_ns=track.mtime_ns, tag_hash=track.tag_hash)

def _operation_attempts(run: ApplyRun) -> dict[int, OperationAttempt]:
    return {attempt.operation_id: attempt for attempt in run.operation_attempts}

def _operations(session: Session, ids: list[int]) -> list[Operation]:
    if not ids:
        return []
    by_id = {op.id: op for op in session.scalars(select(Operation).where(Operation.id.in_(ids)))}
    missing = [op_id for op_id in ids if op_id not in by_id]
    if missing:
        raise BundleApplyError(f"apply manifest references missing operations: {missing}")
    return [by_id[op_id] for op_id in ids]

def _refresh_manifest(run: ApplyRun, files: list[dict[str, object]]) -> None:
    manifest = dict(run.manifest)
    manifest["files"] = files
    run.manifest = manifest
    flag_modified(run, "manifest")

def _file_result(entry: dict[str, object], attempts: dict[int, OperationAttempt]) -> FileApplyResult:
    operation_ids = [op_id for op_id in cast(list[object], entry.get("operation_ids", [])) if isinstance(op_id, int)]
    return FileApplyResult(
        track_id=cast(int, entry["track_id"]),
        state=str(entry.get("state", "failed")),
        applied_operation_ids=tuple(op_id for op_id in operation_ids if attempts[op_id].state == "applied"),
        error=cast(str | None, entry.get("error")),
    )

def _collection_identity(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()

def _recount_groups(session: Session, group_ids: set[int]) -> None:
    for group_id in group_ids:
        group = session.get(TrackGroup, group_id)
        if group is None:
            continue
        group.track_count = sum(1 for _ in session.scalars(select(Track.id).where(Track.group_id == group_id)))

def _apply_grouping_correction(session: Session, track: Track, operations: list[Operation], attempts: dict[int, OperationAttempt]) -> tuple[str, str | None]:
    if len(operations) != 1:
        return "failed", "grouping review must contain one selected correction"
    operation = operations[0]
    value = operation.proposed_value
    current = operation.current_value
    if not isinstance(value, dict) or not isinstance(current, dict):
        return "failed", "grouping correction payload is invalid"
    source_group_id = value.get("source_group_id")
    current_group_id = current.get("group_id")
    action = value.get("action")
    if not isinstance(source_group_id, int) or source_group_id != current_group_id or track.group_id != source_group_id:
        return "failed", "collection changed after preview; refresh the review"
    source = session.get(TrackGroup, source_group_id)
    if source is None or source.is_pinned:
        return "failed", "collection changed after preview; refresh the review"
    dirty_groups = {source.id}
    if action == "confirm_collection":
        source.is_pinned = True
    elif action == "treat_as_singleton":
        singleton_key = value.get("singleton_key")
        expected_key = blake2b(f"resolver-singleton:{track.id}".encode()).hexdigest()[:32]
        if singleton_key != expected_key:
            return "failed", "singleton correction key is invalid"
        target = session.scalar(select(TrackGroup).where(TrackGroup.key == singleton_key))
        if target is None:
            target = TrackGroup(key=singleton_key, kind="singleton", grouping_basis="manual", grouping_confidence=1.0, track_count=0)
            session.add(target)
            session.flush()
        if target.kind != "singleton" or target.grouping_basis != "manual":
            return "failed", "singleton correction target is invalid"
        track.group_id = target.id
        target.is_pinned = True
        dirty_groups.add(target.id)
    elif action == "move_to_collection":
        target_id = value.get("to_group_id")
        if not isinstance(target_id, int) or target_id == source.id:
            return "failed", "collection correction target is invalid"
        target = session.get(TrackGroup, target_id)
        track_artist = _collection_identity(track.album_artist or track.artist)
        if target is None or not _collection_identity(track.album) or not track_artist or _collection_identity(track.album) != _collection_identity(target.album) or track_artist != _collection_identity(target.album_artist):
            return "failed", "collection correction target is no longer compatible"
        track.group_id = target.id
        target.is_pinned = True
        dirty_groups.add(target.id)
    else:
        return "failed", "unsupported grouping correction action"
    session.flush()
    _recount_groups(session, dirty_groups)
    attempt = attempts[operation.id]
    attempt.state = "applied"
    attempt.error = None
    return "applied", None

def apply_review_run(session: Session, apply_run_id: int, *, library_root: Path, create_directories: bool = False, blob_store: BlobStore | None = None, backup_store: BackupStore | None = None, should_cancel: Callable[[], bool] | None = None) -> BundleApplyResult:
    run = session.get(ApplyRun, apply_run_id)
    if run is None:
        raise BundleApplyError(f"apply run {apply_run_id} not found")
    bundle = session.get(ReviewBundle, run.review_bundle_id)
    if bundle is None:
        raise BundleApplyError("apply run has no review bundle")
    files = _manifest_files(run)
    attempts = _operation_attempts(run)
    if run.state == "applied":
        results = tuple(_file_result(entry, attempts) for entry in files)
        return BundleApplyResult(run.id, bundle.id, "applied", results)
    run.state = "applying"
    if bundle.state != "applying":
        bundle.state = "applying"
    _sync_inbox_state(session, bundle)
    session.commit()
    cancelled = False
    for entry in files:
        if should_cancel is not None and should_cancel():
            cancelled = True
            for pending_entry in files:
                if pending_entry.get("state") not in {"applied", "skipped"}:
                    pending_entry["state"] = "failed"
                    pending_entry["error"] = "cancelled before file apply"
            _refresh_manifest(run, files)
            session.commit()
            break
        operation_ids_for_entry = [op_id for op_id in cast(list[object], entry.get("operation_ids", [])) if isinstance(op_id, int)]
        if operation_ids_for_entry and all(attempts[op_id].state == "applied" for op_id in operation_ids_for_entry):
            entry["state"] = "applied"
            entry["error"] = None
        if entry.get("state") in {"applied", "skipped"}:
            continue
        track_id = cast(int, entry["track_id"])
        operation_ids = [op_id for op_id in cast(list[object], entry.get("operation_ids", [])) if isinstance(op_id, int)]
        retry_ids = [op_id for op_id in operation_ids if attempts[op_id].state in {"pending", "failed"}]
        if not retry_ids:
            entry["state"] = "applied" if all(attempts[op_id].state == "applied" for op_id in operation_ids) else "skipped"
            continue
        track = session.get(Track, track_id)
        if track is None:
            missing_error = f"track {track_id} not found"
            entry["state"] = "failed"
            entry["error"] = missing_error
            for op_id in retry_ids:
                attempts[op_id].state = "failed"
                attempts[op_id].error = missing_error
            _refresh_manifest(run, files)
            session.commit()
            continue
        operations = _operations(session, retry_ids)
        validation_errors: list[str] = []
        for operation in operations:
            raw_errors = operation.validation.get("errors", [])
            if isinstance(raw_errors, list):
                validation_errors.extend(str(e) for e in raw_errors if e)
            if operation.validation.get("collision") is True:
                validation_errors.append("unresolved destination collision")
        if validation_errors:
            validation_error = "; ".join(validation_errors)
            entry["state"] = "skipped"
            entry["error"] = validation_error
            for operation in operations:
                attempts[operation.id].state = "skipped"
                attempts[operation.id].error = validation_error
            _refresh_manifest(run, files)
            session.commit()
            continue
        grouping_ops = [op for op in operations if op.kind == OperationKind.GROUPING_CORRECTION.value]
        if grouping_ops:
            if len(grouping_ops) != len(operations):
                mixed_error = "grouping corrections cannot be mixed with file operations"
                entry["state"] = "failed"
                entry["error"] = mixed_error
                for operation in operations:
                    attempts[operation.id].state = "failed"
                    attempts[operation.id].error = mixed_error
                _refresh_manifest(run, files)
                session.commit()
                continue
            file_state, grouping_error = _apply_grouping_correction(session, track, grouping_ops, attempts)
            entry["state"] = file_state
            entry["error"] = grouping_error
            if grouping_error is not None:
                for op in grouping_ops:
                    attempts[op.id].state = "failed"
                    attempts[op.id].error = grouping_error
            _refresh_manifest(run, files)
            session.commit()
            continue
        # Determine precondition: if any operation already applied, use current, else source
        already_applied = any(attempts[op_id].state == "applied" for op_id in operation_ids)
        # For simplicity, if retry after partial success, use current precondition
        # else use source snapshot
        try:
            if already_applied:
                precondition = _current_precondition(track)
            else:
                precondition = _source_precondition(entry.get("source"))
        except BundleApplyError as exc:
            entry["state"] = "skipped"
            entry["error"] = str(exc)
            for op_id in retry_ids:
                attempts[op_id].state = "conflicted"
                attempts[op_id].error = str(exc)
            _refresh_manifest(run, files)
            session.commit()
            continue
        # Partition operations into tag vs move
        tag_ops = [op for op in operations if op.kind != OperationKind.MOVE_FILE.value]
        move_ops = [op for op in operations if op.kind == OperationKind.MOVE_FILE.value]
        file_state = "applied"
        file_error: str | None = None
        if tag_ops:
            field_values: dict[str, object] = {}
            art_blob_id: int | None = None
            remove_art = False
            lyrics_payload: dict[str, object] | None = None
            lyrics_remove = False
            for op in tag_ops:
                if op.kind == OperationKind.SET_TAG.value or op.kind == OperationKind.SET_REPLAY_GAIN.value:
                    field_values[op.field] = op.proposed_value
                elif op.kind == OperationKind.EMBED_ART.value:
                    if isinstance(op.proposed_value, dict) and isinstance(op.proposed_value.get("blob_id"), int):
                        art_blob_id = op.proposed_value["blob_id"]
                elif op.kind == OperationKind.REMOVE_ART.value:
                    remove_art = True
                elif op.kind == OperationKind.WRITE_LYRICS.value:
                    if isinstance(op.proposed_value, dict):
                        lyrics_payload = op.proposed_value
                elif op.kind == OperationKind.GROUPING_CORRECTION.value:
                    pass
            ok, err = write_tag_fields(session, apply_run_id=run.id, track=track, field_values=field_values, art_blob_id=art_blob_id, remove_art=remove_art, lyrics_payload=lyrics_payload, lyrics_remove=lyrics_remove, blob_store=blob_store, backup_store=backup_store, source_precondition=precondition, library_root=library_root)
            if not ok:
                file_state = "failed"
                file_error = err
                for op in tag_ops:
                    if attempts[op.id].state != "applied":
                        attempts[op.id].state = "failed" if "snapshot" not in (err or "") else "conflicted"
                        attempts[op.id].error = err
            else:
                for op in tag_ops:
                    attempts[op.id].state = "applied"
                    attempts[op.id].error = None
        if file_state == "applied" and move_ops:
            # move ops: there should be at most one
            move_op = move_ops[0]
            dest = move_op.proposed_value
            if not isinstance(dest, str):
                file_state = "failed"
                file_error = "move_file requires string destination"
                for op in move_ops:
                    attempts[op.id].state = "failed"
                    attempts[op.id].error = file_error
            else:
                ok, err = write_move(session, apply_run_id=run.id, track=track, destination=dest, library_root=library_root, create_directories=create_directories)
                if not ok:
                    file_state = "failed"
                    file_error = err
                    for op in move_ops:
                        attempts[op.id].state = "failed"
                        attempts[op.id].error = err
                else:
                    for op in move_ops:
                        attempts[op.id].state = "applied"
                        attempts[op.id].error = None
        entry["state"] = file_state
        entry["error"] = file_error
        _refresh_manifest(run, files)
        session.commit()
    applied_count = sum(1 for a in attempts.values() if a.state == "applied")
    total = len(attempts)
    if total == 0:
        final_state = "failed"
    elif applied_count == total:
        final_state = "applied"
    elif applied_count == 0:
        final_state = "failed"
    else:
        final_state = "partially_applied"
    results = tuple(_file_result(entry, attempts) for entry in files)
    errors = {r.track_id: r.error for r in results if r.error is not None}
    run.state = final_state
    run.result = {"state": final_state, "atomicity": "per_file", "files": [{"track_id": r.track_id, "state": r.state, "applied_operation_ids": list(r.applied_operation_ids), "error": r.error} for r in results]}
    run.error = "; ".join(f"track {tid}: {err}" for tid, err in errors.items()) or None
    bundle.state = final_state
    bundle.error = run.error
    _sync_inbox_state(session, bundle)
    _refresh_manifest(run, files)
    session.commit()
    return BundleApplyResult(run.id, bundle.id, final_state, results, errors, cancelled=cancelled)

def build_undo_changesets_for_run(session: Session, apply_run_id: int) -> tuple[object, ...]:
    # Native undo not via ChangeSet; return empty tuple for compatibility
    # Real undo is via ReviewUndoRun
    return ()
