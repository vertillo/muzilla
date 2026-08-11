"""Persistent per-file execution of a frozen ReviewBundle undo run.

Inverse ChangeSets are still applied by :mod:`muzilla.changes.applier`; this module
only owns checkpoints, retry selection and crash reconciliation.  It deliberately does
not introduce another file writer or claim cross-file atomicity.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from muzilla.changes.applier import SourcePrecondition, apply_changeset
from muzilla.changes.backup import BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.undo import build_undo_changeset, mark_frozen_review_undo
from muzilla.db.models import (
    ApplyJournal,
    ChangeSet,
    ReviewUndoRun,
    Track,
)


class BundleUndoError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FileUndoResult:
    track_id: int
    state: str
    """undone | failed | pending"""
    source_change_set_ids: tuple[int, ...] = ()
    error: str | None = None
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class BundleUndoResult:
    undo_run_id: int
    review_bundle_id: int
    source_apply_run_id: int
    state: str
    """undone | partially_undone | failed"""
    files: tuple[FileUndoResult, ...] = ()
    errors: dict[int, str] = dc_field(default_factory=dict)
    cancelled: bool = False


def _manifest_files(run: ReviewUndoRun) -> list[dict[str, object]]:
    raw_files = run.manifest.get("files")
    if not isinstance(raw_files, list):
        raise BundleUndoError("undo manifest has no per-file entries")
    files: list[dict[str, object]] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or not isinstance(raw_file.get("track_id"), int):
            raise BundleUndoError("undo manifest contains an invalid file entry")
        if not isinstance(raw_file.get("steps"), list):
            raise BundleUndoError("undo manifest contains an invalid step list")
        files.append(cast(dict[str, object], raw_file))
    return files


def _source_precondition(raw: object) -> SourcePrecondition:
    if not isinstance(raw, dict):
        raise BundleUndoError("undo source checkpoint is missing")
    path = raw.get("path")
    size_bytes = raw.get("size_bytes")
    mtime_ns = raw.get("mtime_ns")
    tag_hash = raw.get("tag_hash")
    if (
        not isinstance(path, str)
        or not isinstance(size_bytes, int)
        or not isinstance(mtime_ns, int)
        or not isinstance(tag_hash, str)
    ):
        raise BundleUndoError("undo source checkpoint is incomplete")
    return SourcePrecondition(path, size_bytes, mtime_ns, tag_hash)


def _track_checkpoint(track: Track) -> dict[str, object]:
    if track.tag_hash is None:
        raise BundleUndoError("catalog has no tag hash for undo checkpoint")
    return {
        "path": track.path,
        "size_bytes": track.size_bytes,
        "mtime_ns": track.mtime_ns,
        "tag_hash": track.tag_hash,
    }


def _steps(entry: dict[str, object]) -> list[dict[str, object]]:
    raw_steps = cast(list[object], entry["steps"])
    steps: list[dict[str, object]] = []
    for raw_step in raw_steps:
        if (
            not isinstance(raw_step, dict)
            or not isinstance(raw_step.get("source_change_set_id"), int)
            or not isinstance(raw_step.get("undo_change_set_ids"), list)
        ):
            raise BundleUndoError("undo manifest contains an invalid step")
        steps.append(cast(dict[str, object], raw_step))
    return steps


def _refresh_manifest(run: ReviewUndoRun, files: list[dict[str, object]]) -> None:
    manifest = dict(run.manifest)
    manifest["files"] = files
    run.manifest = manifest
    # ``files`` contains the same nested dicts loaded from the JSON column and
    # is intentionally checkpointed in place.  SQLAlchemy otherwise compares
    # the reassigned value with an already-mutated original and can omit the
    # manifest from UPDATEs, losing progress when the next worker opens a new
    # session.
    flag_modified(run, "manifest")


def _recovery_uncertain(session: Session, changeset: ChangeSet) -> bool:
    journals = list(
        session.scalars(
            select(ApplyJournal).where(ApplyJournal.change_set_id == changeset.id)
        )
    )
    if any(journal.state in {"pending", "writing"} for journal in journals):
        return True
    messages = [changeset.error or "", *(journal.error or "" for journal in journals)]
    return any(
        "recovery" in message.casefold() or "manual inspection" in message.casefold()
        for message in messages
    )


def _all_accepted_applied(changeset: ChangeSet) -> bool:
    accepted = [change for change in changeset.changes if change.decision == "accepted"]
    return bool(accepted) and all(change.apply_state == "applied" for change in accepted)


def _changeset_for_step(
    session: Session,
    step: dict[str, object],
    *,
    review_undo_run_id: int,
) -> ChangeSet | None:
    raw_ids = cast(list[object], step["undo_change_set_ids"])
    ids = [value for value in raw_ids if isinstance(value, int)]
    latest = session.get(ChangeSet, ids[-1]) if ids else None
    if latest is not None and (latest.state == "applied" or _all_accepted_applied(latest)):
        latest.state = "applied"
        step["state"] = "undone"
        step["error"] = None
        return None
    if latest is not None and _recovery_uncertain(session, latest):
        raise BundleUndoError(
            "undo recovery is uncertain; manual inspection is required"
        )
    if latest is not None and latest.state == "draft":
        return latest

    source_id = cast(int, step["source_change_set_id"])
    inverse = build_undo_changeset(session, source_id)
    mark_frozen_review_undo(inverse, review_undo_run_id=review_undo_run_id)
    ids.append(inverse.id)
    step["undo_change_set_ids"] = ids
    return inverse


def _retryable_error(error: str | None) -> bool:
    value = (error or "").casefold()
    if "destination already exists" in value or "collision" in value:
        return True
    non_retryable = (
        "source snapshot",
        "no longer exists",
        "outside library root",
        "symlink",
        "tag hash",
        "recovery",
        "journal",
        "manual inspection",
        "not found",
    )
    return not any(token in value for token in non_retryable)


def _file_result(entry: dict[str, object]) -> FileUndoResult:
    return FileUndoResult(
        track_id=cast(int, entry["track_id"]),
        state=str(entry.get("state", "failed")),
        source_change_set_ids=tuple(
            cast(int, step["source_change_set_id"]) for step in _steps(entry)
        ),
        error=cast(str | None, entry.get("error")),
        retryable=entry.get("retryable") is True,
    )


def apply_review_undo_run(
    session: Session,
    undo_run_id: int,
    *,
    library_root: Path,
    create_directories: bool = False,
    blob_store: BlobStore | None = None,
    backup_store: BackupStore | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> BundleUndoResult:
    """Apply or resume a frozen undo without repeating restored files."""
    run = session.get(ReviewUndoRun, undo_run_id)
    if run is None:
        raise BundleUndoError(f"undo run {undo_run_id} not found")
    files = _manifest_files(run)
    if run.state == "undone":
        return BundleUndoResult(
            run.id,
            run.review_bundle_id,
            run.source_apply_run_id,
            "undone",
            tuple(_file_result(entry) for entry in files),
        )

    run.state = "undoing"
    session.commit()
    cancelled = False
    for entry in files:
        if entry.get("state") == "undone":
            continue
        if entry.get("state") == "failed" and entry.get("retryable") is not True:
            continue
        if should_cancel is not None and should_cancel():
            cancelled = True
            break
        entry["state"] = "undoing"
        entry["error"] = None
        entry["retryable"] = False
        _refresh_manifest(run, files)
        session.commit()

        track_id = cast(int, entry["track_id"])
        track = session.get(Track, track_id)
        if track is None:
            entry["state"] = "failed"
            entry["error"] = f"track {track_id} not found"
            _refresh_manifest(run, files)
            session.commit()
            continue

        checkpoint_raw = entry.get("checkpoint", entry.get("source"))
        try:
            checkpoint = _source_precondition(checkpoint_raw)
            for step in _steps(entry):
                if step.get("state") == "undone":
                    continue
                inverse = _changeset_for_step(
                    session,
                    step,
                    review_undo_run_id=run.id,
                )
                if inverse is None:
                    entry["checkpoint"] = _track_checkpoint(track)
                    checkpoint = _source_precondition(entry["checkpoint"])
                    continue
                step["state"] = "undoing"
                step["error"] = None
                _refresh_manifest(run, files)
                session.commit()
                result = apply_changeset(
                    session,
                    inverse.id,
                    library_root=library_root,
                    create_directories=create_directories,
                    blob_store=blob_store,
                    backup_store=backup_store,
                    source_preconditions={track_id: checkpoint},
                )
                session.commit()
                if result.state != "applied":
                    error = result.errors.get(track_id) or "file undo failed"
                    step["state"] = "failed"
                    step["error"] = error
                    entry["state"] = "failed"
                    entry["error"] = error
                    entry["retryable"] = _retryable_error(error)
                    break
                step["state"] = "undone"
                step["error"] = None
                entry["checkpoint"] = _track_checkpoint(track)
                checkpoint = _source_precondition(entry["checkpoint"])
            else:
                entry["state"] = "undone"
                entry["error"] = None
                entry["retryable"] = False
        except (BundleUndoError, OSError, ValueError) as exc:
            entry["state"] = "failed"
            entry["error"] = str(exc)
            entry["retryable"] = _retryable_error(str(exc))
        _refresh_manifest(run, files)
        session.commit()

    results = tuple(_file_result(entry) for entry in files)
    undone_count = sum(result.state == "undone" for result in results)
    if undone_count == len(results):
        final_state = "undone"
    elif undone_count:
        final_state = "partially_undone"
    else:
        final_state = "failed"
    errors = {
        result.track_id: result.error
        for result in results
        if result.error is not None
    }
    run.state = final_state
    run.result = {
        "state": final_state,
        "atomicity": "per_file",
        "files": [
            {
                "track_id": result.track_id,
                "state": result.state,
                "source_change_set_ids": list(result.source_change_set_ids),
                "error": result.error,
                "retryable": result.retryable,
            }
            for result in results
        ],
    }
    run.error = "; ".join(
        f"track {track_id}: {error}" for track_id, error in errors.items()
    ) or None
    _refresh_manifest(run, files)
    session.commit()
    return BundleUndoResult(
        run.id,
        run.review_bundle_id,
        run.source_apply_run_id,
        final_state,
        results,
        errors,
        cancelled=cancelled,
    )
