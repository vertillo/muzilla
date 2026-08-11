"""Controlled per-file execution of a frozen ReviewBundle ApplyRun.

The legacy applier remains the sole file writer.  This module owns only the persistent
bundle manifest, per-operation outcomes, retry selection, and aggregation of per-file
results.  A bundle is deliberately not presented as a cross-file transaction.
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

from muzilla.changes.applier import (
    RECOVERY_RESTORED_MESSAGE,
    SourcePrecondition,
    apply_changeset,
)
from muzilla.changes.backup import BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.builder import build_changeset
from muzilla.changes.review_adapter import field_edit_for_operation
from muzilla.changes.undo import build_undo_changeset
from muzilla.db.models import (
    ApplyJournal,
    ApplyRun,
    Change,
    ChangeSet,
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
    """Keep the disposable inbox projection consistent at apply commit boundaries."""
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
    """applied | failed | skipped"""
    applied_operation_ids: tuple[int, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BundleApplyResult:
    apply_run_id: int
    review_bundle_id: int
    state: str
    """applied | partially_applied | failed"""
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
    if (
        not isinstance(path, str)
        or not isinstance(size_bytes, int)
        or not isinstance(mtime_ns, int)
        or not isinstance(tag_hash, str)
    ):
        raise BundleApplyError("source snapshot is incomplete for file")
    return SourcePrecondition(
        path=path,
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        tag_hash=tag_hash,
    )


def _current_precondition(track: Track) -> SourcePrecondition:
    if track.tag_hash is None:
        raise BundleApplyError("catalog has no current tag hash for retry")
    return SourcePrecondition(
        path=track.path,
        size_bytes=track.size_bytes,
        mtime_ns=track.mtime_ns,
        tag_hash=track.tag_hash,
    )


def _operation_attempts(run: ApplyRun) -> dict[int, OperationAttempt]:
    return {attempt.operation_id: attempt for attempt in run.operation_attempts}


def _operations(session: Session, ids: list[int]) -> list[Operation]:
    if not ids:
        return []
    by_id = {
        operation.id: operation
        for operation in session.scalars(
            select(Operation).where(Operation.id.in_(ids))
        )
    }
    missing = [operation_id for operation_id in ids if operation_id not in by_id]
    if missing:
        raise BundleApplyError(f"apply manifest references missing operations: {missing}")
    return [by_id[operation_id] for operation_id in ids]


def _materialize_file_changeset(
    session: Session,
    run: ApplyRun,
    track_id: int,
    operations: list[Operation],
) -> ChangeSet:
    changeset = build_changeset(
        session,
        title=f"Apply review {run.review_bundle.title}: track {track_id}",
        source="manual_edit",
        edits={track_id: [field_edit_for_operation(operation) for operation in operations]},
        source_ref={
            "review_bundle_id": str(run.review_bundle_id),
            "proposal_revision_id": str(run.proposal_revision_id),
            "apply_run_id": str(run.id),
            "operation_ids": ",".join(str(operation.id) for operation in operations),
        },
        scope_type="track",
        scope_id=track_id,
        created_by="review_bundle",
        candidate_source=run.proposal_revision.candidate_source,
        candidate_ref=run.proposal_revision.candidate_ref,
    )
    for change in changeset.changes:
        change.decision = "accepted"
    changeset.stats = {
        "total": len(changeset.changes),
        "accepted": len(changeset.changes),
        "rejected": 0,
        "pending": 0,
    }
    session.flush()
    return changeset


def _refresh_manifest(run: ApplyRun, files: list[dict[str, object]]) -> None:
    manifest = dict(run.manifest)
    manifest["files"] = files
    run.manifest = manifest
    # ``files`` is the same nested list originally read from the JSON column.
    # Its entries are updated in place throughout an apply, so assigning a
    # shallowly-copied but equal dict is not enough for SQLAlchemy to emit an
    # UPDATE.  Persist every checkpoint before the worker can crash or restart.
    flag_modified(run, "manifest")


def _sync_attempts(
    operations: list[Operation],
    changes: list[Change],
    attempts: dict[int, OperationAttempt],
) -> tuple[str, str | None]:
    if len(operations) != len(changes):
        raise BundleApplyError("legacy applier adapter changed operation cardinality")
    errors: list[str] = []
    states: list[str] = []
    for operation, change in zip(operations, changes, strict=True):
        attempt = attempts[operation.id]
        state = change.apply_state
        if state == "applied":
            attempt.state = "applied"
            attempt.error = None
        elif state == "conflicted":
            attempt.state = "conflicted"
            attempt.error = "source snapshot precondition failed"
        else:
            attempt.state = "failed"
            attempt.error = "legacy file operation failed"
        states.append(attempt.state)
        if attempt.error:
            errors.append(attempt.error)
    if states and all(state == "applied" for state in states):
        return "applied", None
    if states and all(state == "conflicted" for state in states):
        return "skipped", "; ".join(errors)
    return "failed", "; ".join(errors) or "file apply failed"


def _file_result(entry: dict[str, object], attempts: dict[int, OperationAttempt]) -> FileApplyResult:
    operation_ids = [
        operation_id
        for operation_id in cast(list[object], entry.get("operation_ids", []))
        if isinstance(operation_id, int)
    ]
    return FileApplyResult(
        track_id=cast(int, entry["track_id"]),
        state=str(entry.get("state", "failed")),
        applied_operation_ids=tuple(
            operation_id
            for operation_id in operation_ids
            if attempts[operation_id].state == "applied"
        ),
        error=cast(str | None, entry.get("error")),
    )


def _collection_identity(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _recount_groups(session: Session, group_ids: set[int]) -> None:
    for group_id in group_ids:
        group = session.get(TrackGroup, group_id)
        if group is None:
            continue
        group.track_count = sum(
            1 for _ in session.scalars(select(Track.id).where(Track.group_id == group_id))
        )


def _apply_grouping_correction(
    session: Session,
    track: Track,
    operations: list[Operation],
    attempts: dict[int, OperationAttempt],
) -> tuple[str, str | None]:
    """Apply the frozen constrained choice without reaching the file writer.

    The operation was previewed as compatible, but compatibility is checked against the
    current catalog again so a stale review cannot become an arbitrary reassignment.
    """
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
    if (
        not isinstance(source_group_id, int)
        or source_group_id != current_group_id
        or track.group_id != source_group_id
    ):
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
            target = TrackGroup(
                key=singleton_key,
                kind="singleton",
                grouping_basis="manual",
                grouping_confidence=1.0,
                track_count=0,
            )
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
        if (
            target is None
            or not _collection_identity(track.album)
            or not track_artist
            or _collection_identity(track.album) != _collection_identity(target.album)
            or track_artist != _collection_identity(target.album_artist)
        ):
            return "failed", "collection correction target is no longer compatible"
        track.group_id = target.id
        target.is_pinned = True
        dirty_groups.add(target.id)
    else:
        return "failed", "unsupported grouping correction action"

    # The count query below must observe the just-assigned ``track.group_id``;
    # relying on a later implicit autoflush is too subtle for this DB-only apply path.
    session.flush()
    _recount_groups(session, dirty_groups)
    attempt = attempts[operation.id]
    attempt.state = "applied"
    attempt.error = None
    return "applied", None


def _reconcile_completed_journals(
    session: Session,
    entry: dict[str, object],
    attempts: dict[int, OperationAttempt],
) -> bool:
    """Reflect recovery-confirmed legacy changes into the frozen operation attempts."""
    restored_side_effect = False
    for raw_changeset_id in cast(list[object], entry.get("change_set_ids", [])):
        if not isinstance(raw_changeset_id, int):
            continue
        changeset = session.get(ChangeSet, raw_changeset_id)
        if changeset is None:
            continue
        restored_side_effect = restored_side_effect or session.scalar(
            select(ApplyJournal.id).where(
                ApplyJournal.change_set_id == raw_changeset_id,
                ApplyJournal.state == "reverted",
                ApplyJournal.error == RECOVERY_RESTORED_MESSAGE,
            )
        ) is not None
        raw_operation_ids = changeset.source_ref.get("operation_ids", "")
        operation_ids = [
            int(part) for part in raw_operation_ids.split(",") if part.isdigit()
        ]
        changes = sorted(changeset.changes, key=lambda change: change.seq)
        if len(operation_ids) != len(changes):
            continue
        for operation_id, change in zip(operation_ids, changes, strict=True):
            attempt = attempts.get(operation_id)
            if attempt is None:
                continue
            if change.apply_state == "applied":
                attempt.state = "applied"
                attempt.error = None
            elif change.apply_state == "conflicted":
                attempt.state = "conflicted"
                attempt.error = "source snapshot precondition failed"
    return restored_side_effect


def apply_review_run(
    session: Session,
    apply_run_id: int,
    *,
    library_root: Path,
    create_directories: bool = False,
    blob_store: BlobStore | None = None,
    backup_store: BackupStore | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> BundleApplyResult:
    """Apply or resume one frozen run without repeating successful operations."""
    run = session.get(ApplyRun, apply_run_id)
    if run is None:
        raise BundleApplyError(f"apply run {apply_run_id} not found")
    bundle = session.get(ReviewBundle, run.review_bundle_id)
    if bundle is None:  # pragma: no cover - protected by FK
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
        restored_side_effect = _reconcile_completed_journals(session, entry, attempts)
        operation_ids_for_entry = [
            operation_id
            for operation_id in cast(list[object], entry.get("operation_ids", []))
            if isinstance(operation_id, int)
        ]
        if operation_ids_for_entry and all(
            attempts[operation_id].state == "applied"
            for operation_id in operation_ids_for_entry
        ):
            entry["state"] = "applied"
            entry["error"] = None
        if entry.get("state") in {"applied", "skipped"}:
            continue
        track_id = cast(int, entry["track_id"])
        operation_ids = [
            operation_id
            for operation_id in cast(list[object], entry.get("operation_ids", []))
            if isinstance(operation_id, int)
        ]
        retry_ids = [
            operation_id
            for operation_id in operation_ids
            if attempts[operation_id].state in {"pending", "failed"}
        ]
        if not retry_ids:
            entry["state"] = (
                "applied"
                if all(attempts[operation_id].state == "applied" for operation_id in operation_ids)
                else "skipped"
            )
            continue

        track = session.get(Track, track_id)
        if track is None:
            missing_error = f"track {track_id} not found"
            entry["state"] = "failed"
            entry["error"] = missing_error
            for operation_id in retry_ids:
                attempts[operation_id].state = "failed"
                attempts[operation_id].error = missing_error
            _refresh_manifest(run, files)
            session.commit()
            continue

        operations = _operations(session, retry_ids)
        validation_errors: list[str] = []
        for operation in operations:
            raw_errors = operation.validation.get("errors", [])
            if isinstance(raw_errors, list):
                validation_errors.extend(str(error) for error in raw_errors if error)
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
        grouping_operations = [
            operation
            for operation in operations
            if operation.kind == OperationKind.GROUPING_CORRECTION.value
        ]
        if grouping_operations:
            if len(grouping_operations) != len(operations):
                mixed_error = "grouping corrections cannot be mixed with file operations"
                entry["state"] = "failed"
                entry["error"] = mixed_error
                for operation in operations:
                    attempts[operation.id].state = "failed"
                    attempts[operation.id].error = mixed_error
                _refresh_manifest(run, files)
                session.commit()
                continue
            file_state, grouping_error = _apply_grouping_correction(
                session, track, grouping_operations, attempts
            )
            entry["state"] = file_state
            entry["error"] = grouping_error
            if grouping_error is not None:
                for operation in grouping_operations:
                    attempts[operation.id].state = "failed"
                    attempts[operation.id].error = grouping_error
            _refresh_manifest(run, files)
            session.commit()
            continue
        try:
            already_applied = any(
                attempts[operation_id].state == "applied" for operation_id in operation_ids
            )
            precondition = (
                _current_precondition(track)
                if already_applied or restored_side_effect
                else _source_precondition(entry.get("source"))
            )
        except BundleApplyError as exc:
            entry["state"] = "skipped"
            entry["error"] = str(exc)
            for operation_id in retry_ids:
                attempts[operation_id].state = "conflicted"
                attempts[operation_id].error = str(exc)
            _refresh_manifest(run, files)
            session.commit()
            continue

        changeset = _materialize_file_changeset(session, run, track_id, operations)
        change_set_ids = list(cast(list[object], entry.get("change_set_ids", [])))
        change_set_ids.append(changeset.id)
        entry["change_set_ids"] = change_set_ids
        entry["state"] = "applying"
        entry["error"] = None
        _refresh_manifest(run, files)
        session.commit()

        apply_result = apply_changeset(
            session,
            changeset.id,
            library_root=library_root,
            create_directories=create_directories,
            blob_store=blob_store,
            backup_store=backup_store,
            source_preconditions={track_id: precondition},
        )
        changes = list(
            session.scalars(
                select(Change)
                .where(Change.change_set_id == changeset.id)
                .order_by(Change.seq)
            )
        )
        file_state, sync_error = _sync_attempts(operations, changes, attempts)
        entry["state"] = file_state
        entry["error"] = apply_result.errors.get(track_id) or sync_error
        _refresh_manifest(run, files)
        session.commit()

    applied_count = sum(attempt.state == "applied" for attempt in attempts.values())
    unresolved_count = len(attempts) - applied_count
    if unresolved_count == 0:
        final_state = "applied"
    elif applied_count:
        final_state = "partially_applied"
    else:
        final_state = "failed"

    results = tuple(_file_result(entry, attempts) for entry in files)
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
                "applied_operation_ids": list(result.applied_operation_ids),
                "error": result.error,
            }
            for result in results
        ],
    }
    run.error = "; ".join(f"track {track_id}: {error}" for track_id, error in errors.items()) or None
    bundle.state = final_state
    bundle.error = run.error
    _sync_inbox_state(session, bundle)
    _refresh_manifest(run, files)
    session.commit()
    return BundleApplyResult(
        run.id,
        bundle.id,
        final_state,
        results,
        errors,
        cancelled=cancelled,
    )


def build_undo_changesets_for_run(session: Session, apply_run_id: int) -> tuple[ChangeSet, ...]:
    """Build inverse ChangeSets in reverse side-effect order for one bundle run."""
    run = session.get(ApplyRun, apply_run_id)
    if run is None:
        raise BundleApplyError(f"apply run {apply_run_id} not found")
    changeset_ids: list[int] = []
    for entry in _manifest_files(run):
        for raw_id in cast(list[object], entry.get("change_set_ids", [])):
            if isinstance(raw_id, int):
                changeset_ids.append(raw_id)
    undo_changesets: list[ChangeSet] = []
    for changeset_id in reversed(changeset_ids):
        changeset = session.get(ChangeSet, changeset_id)
        if changeset is None:
            raise BundleApplyError(
                f"apply run references missing changeset {changeset_id}"
            )
        applied_changes = [
            change for change in changeset.changes if change.apply_state == "applied"
        ]
        if not applied_changes:
            continue
        if changeset.state == "undo_expired":
            raise BundleApplyError("undo history expired for an applied file")
        if changeset.state not in {"applied", "partially_applied"}:
            raise BundleApplyError(
                "apply recovery is uncertain; inspect the file before undo"
            )
        journals = list(
            session.scalars(
                select(ApplyJournal).where(
                    ApplyJournal.change_set_id == changeset.id
                )
            )
        )
        for change in applied_changes:
            expected_phases = {"move"} if change.op == "move" else {"tags", "art"}
            if not any(
                journal.track_id == change.entity_id
                and journal.phase in expected_phases
                and journal.state == "done"
                for journal in journals
            ):
                raise BundleApplyError(
                    "apply journal is unavailable or recovery is uncertain"
                )
        undo_changesets.append(build_undo_changeset(session, changeset_id))
    if not undo_changesets:
        raise BundleApplyError("apply run has no successful file operations to undo")
    return tuple(undo_changesets)
