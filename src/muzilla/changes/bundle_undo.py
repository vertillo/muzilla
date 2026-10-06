"""Persistent, checkpointed execution of a frozen ReviewBundle undo run."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.backup import BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.db.models import ReviewFileJournal, ReviewUndoRun, Track, WorkUnit
from muzilla.db.transactions import begin_sqlite_write_transaction


class BundleUndoError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class UndoFileResult:
    track_id: int
    state: str
    source_change_set_ids: tuple[int, ...] = ()
    error: str | None = None
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class BundleUndoResult:
    undo_run_id: int
    review_bundle_id: int
    source_apply_run_id: int
    state: str = "undone"
    files: tuple[UndoFileResult, ...] = ()
    errors: dict[int, str] = dc_field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    cancelled: bool = False
    recovery_required: bool = False


def _track_snapshot(track: Track) -> dict[str, object]:
    tag_hash = track.tag_hash
    if not isinstance(tag_hash, str):
        raise BundleUndoError("catalog has no current tag hash for undo")
    return {
        "path": track.path,
        "size_bytes": track.size_bytes,
        "mtime_ns": track.mtime_ns,
        "tag_hash": tag_hash,
    }


def _journal_step(journal: ReviewFileJournal) -> dict[str, object]:
    before_blob = journal.before_blob
    if not isinstance(before_blob, dict):
        raise BundleUndoError("source inverse payload is unavailable")
    phase = journal.phase
    if phase not in {"tags", "move", "grouping"}:
        raise BundleUndoError(f"unsupported source journal phase for undo: {phase}")
    if phase == "tags" and (
        not isinstance(journal.after_hash, str)
        or not isinstance(before_blob.get("__muzilla_physical_guard_before"), dict)
        or not isinstance(before_blob.get("__muzilla_physical_guard_after"), dict)
    ):
        raise BundleUndoError("source tag inverse is incomplete")
    if phase == "move" and (
        not isinstance(journal.before_path, str)
        or not isinstance(journal.after_path, str)
        or not isinstance(before_blob.get("__muzilla_physical_guard_before"), dict)
        or not isinstance(before_blob.get("__muzilla_physical_guard_after"), dict)
    ):
        raise BundleUndoError("source move inverse is incomplete")
    if phase == "grouping":
        group_id = before_blob.get("group_id")
        source_group_id = before_blob.get("source_group_id")
        action = before_blob.get("action")
        if (
            not isinstance(group_id, int)
            or isinstance(group_id, bool)
            or not isinstance(source_group_id, int)
            or isinstance(source_group_id, bool)
            or not isinstance(before_blob.get("source_is_pinned"), bool)
            or action not in {"confirm_collection", "move_to_collection", "treat_as_singleton"}
        ):
            raise BundleUndoError("source grouping inverse is incomplete")
        target_group_id = before_blob.get("target_group_id")
        target_is_pinned = before_blob.get("target_is_pinned")
        if action in {"move_to_collection", "treat_as_singleton"} and (
            not isinstance(target_group_id, int)
            or isinstance(target_group_id, bool)
            or not isinstance(target_is_pinned, bool)
        ):
            raise BundleUndoError("source grouping target inverse is incomplete")
    return {
        "journal_id": journal.id,
        "phase": phase,
        "track_id": journal.track_id,
        "path": journal.path,
        "before_hash": journal.before_hash,
        "after_hash": journal.after_hash,
        "before_path": journal.before_path,
        "after_path": journal.after_path,
        "before_blob": dict(before_blob),
    }


def _manifest_files(manifest: dict[str, object]) -> list[dict[str, object]]:
    raw = manifest.get("files")
    if not isinstance(raw, list):
        raise BundleUndoError("undo manifest has no frozen file list")
    files: list[dict[str, object]] = []
    seen_tracks: set[int] = set()
    seen_journals: set[int] = set()
    for raw_file in raw:
        if not isinstance(raw_file, dict):
            raise BundleUndoError("undo manifest contains an invalid file entry")
        track_id = raw_file.get("track_id")
        source = raw_file.get("source")
        raw_steps = raw_file.get("steps")
        if (
            not isinstance(track_id, int)
            or isinstance(track_id, bool)
            or track_id in seen_tracks
            or not isinstance(source, dict)
            or not isinstance(raw_steps, list)
        ):
            raise BundleUndoError("undo manifest contains an incomplete file entry")
        if not isinstance(source.get("path"), str) or not isinstance(source.get("tag_hash"), str):
            raise BundleUndoError(
                f"undo manifest source checkpoint is incomplete for track {track_id}"
            )
        steps: list[dict[str, object]] = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                raise BundleUndoError(
                    f"undo manifest has an invalid inverse step for track {track_id}"
                )
            journal_id = raw_step.get("journal_id")
            phase = raw_step.get("phase")
            before_blob = raw_step.get("before_blob")
            if (
                not isinstance(journal_id, int)
                or isinstance(journal_id, bool)
                or journal_id in seen_journals
                or phase not in {"tags", "move", "grouping"}
                or not isinstance(before_blob, dict)
                or raw_step.get("track_id", track_id) != track_id
            ):
                raise BundleUndoError(
                    f"undo manifest has an invalid inverse step for track {track_id}"
                )
            seen_journals.add(journal_id)
            steps.append(raw_step)
        if not steps:
            raise BundleUndoError(f"undo manifest has no inverse steps for track {track_id}")
        seen_tracks.add(track_id)
        files.append(raw_file)
    return files


def _execution(manifest: dict[str, object]) -> dict[str, object]:
    raw = manifest.get("execution")
    if not isinstance(raw, dict):
        raise BundleUndoError("undo execution checkpoints are unavailable")
    completed = raw.get("completed_journal_ids")
    active = raw.get("active_journal_id")
    step_checkpoints = raw.get("step_checkpoints")
    file_checkpoints = raw.get("file_checkpoints")
    if (
        not isinstance(completed, list)
        or any(not isinstance(value, int) or isinstance(value, bool) for value in completed)
        or (active is not None and (not isinstance(active, int) or isinstance(active, bool)))
        or not isinstance(step_checkpoints, dict)
        or not isinstance(file_checkpoints, dict)
    ):
        raise BundleUndoError("undo execution checkpoints are invalid")
    return raw


def _is_true(value: object) -> bool:
    return isinstance(value, bool) and value


def _source_step_map(files: list[dict[str, object]]) -> dict[int, tuple[dict[str, object], ...]]:
    result: dict[int, tuple[dict[str, object], ...]] = {}
    for file_entry in files:
        track_id = cast(int, file_entry["track_id"])
        result[track_id] = tuple(cast(list[dict[str, object]], file_entry["steps"]))
    return result


def _freeze_legacy_manifest(
    session: Session,
    run: ReviewUndoRun,
    source_run_id: int,
    journals: list[ReviewFileJournal],
) -> dict[str, object]:
    """Upgrade only manifests whose journal rows prove every completed boundary."""
    old_manifest = run.manifest if isinstance(run.manifest, dict) else {}
    old_files_raw = old_manifest.get("files", [])
    if not isinstance(old_files_raw, list):
        raise BundleUndoError("legacy undo manifest has no file checkpoints")
    old_files = {
        entry.get("track_id"): entry
        for entry in old_files_raw
        if isinstance(entry, dict) and isinstance(entry.get("track_id"), int)
    }
    by_track: dict[int, list[dict[str, object]]] = {}
    completed_ids: list[int] = []
    for journal in journals:
        state = getattr(journal, "state", None)
        if state == "writing":
            raise BundleUndoError(
                "recovery_required: legacy Undo has an uncertain writing checkpoint"
            )
        if state not in {"done", "rolled_back"}:
            raise BundleUndoError("legacy Undo journal state is not a proven checkpoint")
        step = _journal_step(journal)
        track_id = cast(int, step["track_id"])
        by_track.setdefault(track_id, []).append(step)
        if state == "rolled_back":
            completed_ids.append(cast(int, step["journal_id"]))

    files: list[dict[str, object]] = []
    step_checkpoints: dict[str, object] = {}
    file_checkpoints: dict[str, object] = {}
    for track_id, steps in by_track.items():
        track = session.get(Track, track_id)
        old_file = old_files.get(track_id)
        old_source = old_file.get("source") if isinstance(old_file, dict) else None
        if track is None:
            raise BundleUndoError(f"legacy Undo track {track_id} no longer exists")
        if (
            isinstance(old_source, dict)
            and isinstance(old_source.get("path"), str)
            and isinstance(old_source.get("tag_hash"), str)
        ):
            source = dict(old_source)
        elif any(step["journal_id"] in completed_ids for step in steps):
            raise BundleUndoError("legacy Undo cannot prove the original applied file checkpoint")
        else:
            source = _track_snapshot(track)
        files.append(
            {
                "track_id": track_id,
                "source": source,
                "state": "pending",
                "error": None,
                "retryable": True,
                "steps": steps,
            }
        )
        prefix: list[dict[str, object]] = []
        for step in steps:
            if step["journal_id"] not in completed_ids:
                break
            prefix.append(step)
        if prefix:
            last = prefix[-1]
            before_blob = cast(dict[str, object], last["before_blob"])
            guard = before_blob.get("__muzilla_physical_guard_restored")
            if last["phase"] in {"tags", "move"} and not isinstance(guard, dict):
                raise BundleUndoError("legacy Undo completion lacks a restored physical guard")
            checkpoint: dict[str, object] = {
                "path": track.path,
                "track": _track_snapshot(track),
                "physical_guard": guard,
                "phase": last["phase"],
            }
            step_checkpoints[str(last["journal_id"])] = checkpoint
            if len(prefix) == len(steps):
                file_checkpoints[str(track_id)] = {
                    **checkpoint,
                    "completed_journal_ids": [step["journal_id"] for step in steps],
                }

    if not files and old_files_raw:
        raise BundleUndoError("legacy Undo has no journal-backed inverse steps")
    return {
        "version": 2,
        "source_apply_run_id": source_run_id,
        "files": files,
        "execution": {
            "completed_journal_ids": completed_ids,
            "active_journal_id": None,
            "step_checkpoints": step_checkpoints,
            "file_checkpoints": file_checkpoints,
        },
        "job_ids": old_manifest.get("job_ids", []),
    }


_UNDO_MUTABLE_JOURNAL_KEYS = frozenset(
    {
        "__muzilla_case_inverse",
        "__muzilla_physical_guard_restored",
        "__muzilla_publication_transition",
    }
)


def _journal_matches_frozen(
    journal: ReviewFileJournal, step: dict[str, object], track_id: int
) -> bool:
    before_blob = journal.before_blob
    frozen_blob = step.get("before_blob")
    if not isinstance(before_blob, dict) or not isinstance(frozen_blob, dict):
        return False
    return (
        journal.id == step.get("journal_id")
        and journal.track_id == track_id
        and journal.phase == step.get("phase")
        and journal.path == step.get("path")
        and journal.before_hash == step.get("before_hash")
        and journal.after_hash == step.get("after_hash")
        and journal.before_path == step.get("before_path")
        and journal.after_path == step.get("after_path")
        and all(
            before_blob.get(key) == value
            for key, value in frozen_blob.items()
            if key not in _UNDO_MUTABLE_JOURNAL_KEYS
        )
    )


def _group_before_matches(session: Session, track: Track, before: dict[str, object]) -> bool:
    group_id = before.get("group_id")
    source_id = before.get("source_group_id")
    source_pinned = before.get("source_is_pinned")
    target_id = before.get("target_group_id")
    target_pinned = before.get("target_is_pinned")
    if not isinstance(source_id, int) or not isinstance(source_pinned, bool):
        return False
    if getattr(track, "work_unit_id", None) != group_id:
        return False
    source = session.get(WorkUnit, source_id)
    if source is not None and source.is_pinned != source_pinned:
        return False
    if isinstance(target_id, int) and isinstance(target_pinned, bool):
        target = session.get(WorkUnit, target_id)
        if target is not None and target.is_pinned != target_pinned:
            return False
    return True


def _group_applied_matches(session: Session, track: Track, before: dict[str, object]) -> bool:
    source_id = before.get("source_group_id")
    source_pinned = before.get("source_is_pinned")
    action = before.get("action")
    target_id = before.get("target_group_id")
    if not isinstance(source_id, int) or not isinstance(source_pinned, bool):
        return False
    if action == "confirm_collection":
        current_group = source_id
    elif action in {"treat_as_singleton", "move_to_collection"} and isinstance(target_id, int):
        current_group = target_id
    else:
        return False
    if getattr(track, "work_unit_id", None) != current_group:
        return False
    source = session.get(WorkUnit, source_id)
    if source is None:
        return False
    if action == "confirm_collection":
        return source.is_pinned
    if source.is_pinned != source_pinned:
        return False
    target = session.get(WorkUnit, target_id)
    return target is not None and target.is_pinned


def _physical_guard(step: dict[str, object], key: str) -> dict[str, object]:
    before = step.get("before_blob")
    if not isinstance(before, dict):
        raise BundleUndoError("inverse journal payload is invalid")
    guard = before.get(key)
    if not isinstance(guard, dict) or guard.get("version") != 1:
        raise BundleUndoError(f"inverse journal physical guard {key!r} is unavailable")
    return cast(dict[str, object], guard)


def _verify_checkpoint(
    session: Session,
    track: Track,
    step: dict[str, object],
    checkpoint: dict[str, object],
    *,
    library_root: Path,
) -> dict[str, object] | None:
    from muzilla.changes.writer import verify_file_guard

    track_checkpoint = checkpoint.get("track")
    if not isinstance(track_checkpoint, dict) or _track_snapshot(track) != track_checkpoint:
        raise OSError(f"catalog drift detected at Undo checkpoint for track {track.id}")
    path = track.path
    if checkpoint.get("path") != path:
        raise OSError(f"catalog path drift detected at Undo checkpoint for track {track.id}")
    if step.get("phase") == "grouping":
        before = cast(dict[str, object], step.get("before_blob"))
        if not _group_before_matches(session, track, before):
            raise OSError(f"grouping drift detected at Undo checkpoint for track {track.id}")
        return None
    guard = checkpoint.get("physical_guard")
    if not isinstance(guard, dict):
        raise OSError(f"physical Undo checkpoint is missing for track {track.id}")
    return verify_file_guard(Path(path), cast(dict[str, object], guard), library_root)


def _expected_blob_ids(before: dict[str, object]) -> set[int]:
    blob_ids: set[int] = set()
    for key in ("__muzilla_catalog_art_blob_id", "__muzilla_art_blob_id"):
        value = before.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            blob_ids.add(value)
    embedded = before.get("__muzilla_embedded_art")
    if isinstance(embedded, dict):
        entries = embedded.get("entries")
        if not isinstance(entries, list):
            raise OSError("journal artwork entries are invalid")
        for entry in entries:
            if not isinstance(entry, dict):
                raise OSError("journal artwork entry is invalid")
            blob_id = entry.get("blob_id")
            if not isinstance(blob_id, int) or isinstance(blob_id, bool):
                raise OSError("journal artwork blob id is invalid")
            blob_ids.add(blob_id)
    return blob_ids


def _validate_inverse_blobs(
    session: Session, step: dict[str, object], blob_store: BlobStore | None
) -> None:
    before = step.get("before_blob")
    if not isinstance(before, dict):
        raise OSError("inverse journal payload is invalid")
    for blob_id in _expected_blob_ids(cast(dict[str, object], before)):
        if blob_store is None:
            raise OSError("blob store is required by the frozen inverse")
        blob = blob_store.get_by_id(session, blob_id)
        if blob is None:
            raise OSError(f"inverse artwork blob {blob_id} is unavailable")
        blob_store.get_durable_bytes(blob)


def _expected_track_ids(session: Session, source_run_id: int) -> set[int]:
    from muzilla.db.models import Operation, OperationAttempt

    result: set[int] = set()
    for attempt in session.scalars(
        select(OperationAttempt).where(
            OperationAttempt.apply_run_id == source_run_id,
            OperationAttempt.state == "applied",
        )
    ):
        operation = session.get(Operation, attempt.operation_id)
        if operation is not None and operation.target_type == "track":
            result.add(operation.target_id)
    return result


def _active_conflicts(
    session: Session, run_id: int, apply_run_id: int, track_ids: set[int]
) -> str | None:
    from muzilla.db.models import ApplyRun

    for other_apply in session.scalars(
        select(ApplyRun).where(ApplyRun.state.in_(["pending", "applying"]))
    ):
        if other_apply.id == apply_run_id:
            continue
        raw_files = (
            other_apply.manifest.get("files", []) if isinstance(other_apply.manifest, dict) else []
        )
        if isinstance(raw_files, list):
            for entry in raw_files:
                if (
                    isinstance(entry, dict)
                    and isinstance(entry.get("track_id"), int)
                    and entry["track_id"] in track_ids
                ):
                    return f"concurrent review targets track {entry['track_id']}"
    for other_undo in session.scalars(
        select(ReviewUndoRun).where(ReviewUndoRun.state.in_(["pending", "undoing"]))
    ):
        if other_undo.id == run_id:
            continue
        raw_files = (
            other_undo.manifest.get("files", []) if isinstance(other_undo.manifest, dict) else []
        )
        if isinstance(raw_files, list):
            for entry in raw_files:
                if (
                    isinstance(entry, dict)
                    and isinstance(entry.get("track_id"), int)
                    and entry["track_id"] in track_ids
                ):
                    return f"concurrent Undo targets track {entry['track_id']}"
    return None


def _error_result(
    session: Session,
    run: ReviewUndoRun,
    manifest: dict[str, object],
    execution: dict[str, object],
    errors: dict[int, str],
    *,
    cancelled: bool = False,
    recovery_required: bool,
    retryable: bool,
) -> BundleUndoResult:
    files = _manifest_files(manifest)
    file_checkpoints = cast(dict[str, object], execution["file_checkpoints"])
    file_results: list[dict[str, object]] = []
    result_files: list[UndoFileResult] = []
    for file_entry in files:
        track_id = cast(int, file_entry["track_id"])
        state = "undone" if str(track_id) in file_checkpoints else "failed"
        error = errors.get(track_id)
        file_results.append(
            {
                "track_id": track_id,
                "state": state,
                "source_change_set_ids": [],
                "error": error,
                "retryable": retryable,
            }
        )
        result_files.append(
            UndoFileResult(
                track_id=track_id,
                state=state,
                error=error,
                retryable=retryable,
            )
        )
    first_error = next(iter(errors.values()), "Undo cancelled at a complete-file boundary")
    if recovery_required and not first_error.startswith("recovery_required:"):
        first_error = f"recovery_required: {first_error}"
    run.state = "failed"
    run.error = first_error
    result: dict[str, object] = {
        "state": "failed",
        "atomicity": "review_bundle",
        "files": file_results,
        "recovery_required": recovery_required,
        "retryable": retryable,
    }
    if cancelled:
        result["cancelled"] = True
    run.result = result
    session.commit()
    return BundleUndoResult(
        undo_run_id=run.id,
        review_bundle_id=run.review_bundle_id,
        source_apply_run_id=run.source_apply_run_id,
        state="failed",
        files=tuple(result_files),
        errors=dict(errors),
        cancelled=cancelled,
        recovery_required=recovery_required,
    )


def _check_source_journals(
    run: ReviewUndoRun,
    source_run_id: int,
    journals: list[ReviewFileJournal],
    manifest: dict[str, object],
    execution: dict[str, object],
) -> tuple[dict[int, ReviewFileJournal], dict[int, dict[str, object]]]:
    files = _manifest_files(manifest)
    steps_by_track = _source_step_map(files)
    by_id = {journal.id: journal for journal in journals}
    frozen_steps: dict[int, dict[str, object]] = {}
    for track_id, steps in steps_by_track.items():
        for step in steps:
            journal_id = cast(int, step["journal_id"])
            frozen_steps[journal_id] = step
            journal = by_id.get(journal_id)
            if journal is None or not _journal_matches_frozen(journal, step, track_id):
                raise BundleUndoError("undo journal no longer matches its frozen inverse manifest")
    if set(frozen_steps) != set(by_id):
        raise BundleUndoError("source journal set changed after the Undo manifest was frozen")
    completed_values = cast(list[int], execution["completed_journal_ids"])
    completed = set(completed_values)
    if len(completed) != len(completed_values) or not completed.issubset(frozen_steps):
        raise BundleUndoError("undo completion checkpoints do not match the frozen manifest")
    active = execution.get("active_journal_id")
    if active is not None:
        raise BundleUndoError("recovery_required: an Undo filesystem checkpoint is still writing")
    for journal_id, journal in by_id.items():
        expected_state = "rolled_back" if journal_id in completed else "done"
        if getattr(journal, "state", None) != expected_state:
            raise BundleUndoError("undo journal state and durable execution checkpoint disagree")
    return by_id, frozen_steps


def _preflight(
    session: Session,
    run: ReviewUndoRun,
    source_run_id: int,
    journals: list[ReviewFileJournal],
    manifest: dict[str, object],
    execution: dict[str, object],
    *,
    library_root: Path,
    blob_store: BlobStore | None,
    validate_blobs: bool = True,
    check_conflicts: bool = True,
) -> tuple[dict[int, str], bool, dict[int, ReviewFileJournal]]:
    from muzilla.changes.writer import journal_file_guard, verify_file_guard

    errors: dict[int, str] = {}
    unknown = False
    by_id, _frozen_steps = _check_source_journals(run, source_run_id, journals, manifest, execution)
    files = _manifest_files(manifest)
    expected_tracks = _expected_track_ids(session, source_run_id)
    actual_tracks = {journal.track_id for journal in journals}
    if expected_tracks and not actual_tracks.issuperset(expected_tracks):
        missing = sorted(expected_tracks - actual_tracks)
        raise BundleUndoError(f"undo expired: source journals missing for track(s) {missing}")

    if check_conflicts:
        conflict = _active_conflicts(
            session,
            run.id,
            source_run_id,
            {cast(int, entry["track_id"]) for entry in files},
        )
        if conflict is not None:
            return {cast(int, entry["track_id"]): conflict for entry in files}, False, by_id

    completed = set(cast(list[int], execution["completed_journal_ids"]))
    step_checkpoints = cast(dict[str, object], execution["step_checkpoints"])
    file_checkpoints = cast(dict[str, object], execution["file_checkpoints"])
    for file_entry in files:
        track_id = cast(int, file_entry["track_id"])
        steps = cast(list[dict[str, object]], file_entry["steps"])
        track = session.get(Track, track_id)
        if track is None:
            errors[track_id] = f"recovery_required: track {track_id} not found for undo"
            unknown = True
            continue
        prefix_length = 0
        for step in steps:
            if step["journal_id"] not in completed:
                break
            prefix_length += 1
        if any(step["journal_id"] in completed for step in steps[prefix_length:]):
            errors[track_id] = (
                "Undo checkpoints are not a completed prefix of the frozen file steps"
            )
            unknown = True
            continue
        if prefix_length:
            checkpoint: object
            if prefix_length == len(steps):
                checkpoint = file_checkpoints.get(str(track_id))
            else:
                checkpoint = step_checkpoints.get(str(steps[prefix_length - 1]["journal_id"]))
            if not isinstance(checkpoint, dict):
                errors[track_id] = "recovery_required: completed Undo checkpoint is missing"
                unknown = True
                continue
            try:
                _verify_checkpoint(
                    session,
                    track,
                    steps[prefix_length - 1],
                    cast(dict[str, object], checkpoint),
                    library_root=library_root,
                )
            except Exception as exc:
                errors[track_id] = (
                    f"recovery_required: completed-file drift before Undo retry: {exc}"
                )
                unknown = True
            for step in steps[prefix_length:]:
                if not validate_blobs or step.get("phase") != "tags":
                    continue
                try:
                    _validate_inverse_blobs(session, step, blob_store)
                except Exception as exc:
                    errors[track_id] = f"recovery_required: inverse data unavailable: {exc}"
                    break
            continue

        source = cast(dict[str, object], file_entry["source"])
        try:
            if _track_snapshot(track) != source:
                raise OSError(f"catalog source drift detected for track {track_id}")
            physical_steps = [step for step in steps if step.get("phase") in {"tags", "move"}]
            if physical_steps:
                expected_guard = journal_file_guard(
                    cast(dict[str, Any], physical_steps[0]["before_blob"]),
                    "__muzilla_physical_guard_after",
                )
                if expected_guard.get("path") != track.path:
                    raise OSError(f"applied file path changed for track {track_id}")
                verify_file_guard(Path(track.path), expected_guard, library_root)
            grouping_steps = [step for step in steps if step.get("phase") == "grouping"]
            if grouping_steps and not _group_applied_matches(
                session,
                track,
                cast(dict[str, object], grouping_steps[0]["before_blob"]),
            ):
                raise OSError(f"grouping source drift detected for track {track_id}")
        except Exception as exc:
            errors[track_id] = f"source drift or recovery evidence failure: {exc}"
            unknown = True
            continue

        for step in steps:
            journal_id = cast(int, step["journal_id"])
            if journal_id in completed:
                continue
            if validate_blobs and step.get("phase") == "tags":
                try:
                    _validate_inverse_blobs(
                        session,
                        step,
                        blob_store,
                    )
                except Exception as exc:
                    errors[track_id] = f"recovery_required: inverse data unavailable: {exc}"
                    break
    return errors, unknown, by_id


def _cancel_requested(should_cancel: object | None) -> bool:
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel()) if callable(should_cancel) else bool(should_cancel)
    except Exception:
        return False


def _restore_grouping(session: Session, track: Track, before: dict[str, object]) -> None:
    from muzilla.changes.bundle_applier import _recount_groups

    group_id = before.get("group_id")
    source_id = before.get("source_group_id")
    source_pinned = before.get("source_is_pinned")
    target_id = before.get("target_group_id")
    target_pinned = before.get("target_is_pinned")
    if (
        not isinstance(group_id, int)
        or isinstance(group_id, bool)
        or not isinstance(source_id, int)
        or isinstance(source_id, bool)
        or not isinstance(source_pinned, bool)
    ):
        raise OSError("grouping inverse payload is incomplete")
    track.work_unit_id = group_id
    source = session.get(WorkUnit, source_id)
    if source is not None:
        source.is_pinned = source_pinned
    if isinstance(target_id, int) and isinstance(target_pinned, bool):
        target = session.get(WorkUnit, target_id)
        if target is not None:
            target.is_pinned = target_pinned
    affected = {source_id}
    if isinstance(group_id, int):
        affected.add(group_id)
    if isinstance(target_id, int):
        affected.add(target_id)
    _recount_groups(session, affected)


def _set_execution(
    run: ReviewUndoRun, manifest: dict[str, object], execution: dict[str, object]
) -> None:
    updated = copy.deepcopy(manifest)
    updated["execution"] = copy.deepcopy(execution)
    run.manifest = updated


def _set_step_checkpoint(
    run: ReviewUndoRun,
    manifest: dict[str, object],
    execution: dict[str, object],
    file_entry: dict[str, object],
    step: dict[str, object],
    track: Track,
    restored_guard: dict[str, object] | None,
    *,
    complete_file: bool,
) -> None:
    journal_id = cast(int, step["journal_id"])
    track_id = cast(int, file_entry["track_id"])
    checkpoint: dict[str, object] = {
        "path": track.path,
        "track": _track_snapshot(track),
        "physical_guard": restored_guard,
        "phase": step["phase"],
    }
    step_checkpoints = cast(dict[str, object], execution["step_checkpoints"])
    step_checkpoints[str(journal_id)] = checkpoint
    completed = cast(list[int], execution["completed_journal_ids"])
    if journal_id not in completed:
        completed.append(journal_id)
    execution["active_journal_id"] = None
    if complete_file:
        file_checkpoints = cast(dict[str, object], execution["file_checkpoints"])
        file_checkpoints[str(track_id)] = {
            **checkpoint,
            "completed_journal_ids": [
                entry["journal_id"] for entry in cast(list[dict[str, object]], file_entry["steps"])
            ],
        }
    _set_execution(run, manifest, execution)


def _apply_step(
    session: Session,
    run: ReviewUndoRun,
    manifest: dict[str, object],
    execution: dict[str, object],
    file_entry: dict[str, object],
    step: dict[str, object],
    journal: ReviewFileJournal,
    track: Track,
    *,
    library_root: Path,
    blob_store: BlobStore | None,
) -> None:
    from muzilla.changes.writer import (
        _mark_publication_transition_complete,
        _update_track_file_facts,
        finalize_publication_transition,
        journal_file_guard,
        move_file_with_guard,
        restore_catalog_art_identity,
        restore_from_before_blob,
    )
    from muzilla.domain.metadata import tag_hash as compute_tag_hash
    from muzilla.tags.reader import read_track

    journal_id = cast(int, step["journal_id"])
    execution["active_journal_id"] = journal_id
    journal.state = "writing"
    _set_execution(run, manifest, execution)
    session.commit()

    phase = step["phase"]
    before = cast(dict[str, object], step["before_blob"])
    restored_guard: dict[str, object] | None = None
    try:
        if phase == "tags":
            path = Path(track.path)
            if not path.exists() or path.is_symlink():
                raise OSError(f"file missing or unsafe for undo tags: {path}")
            expected_guard = journal_file_guard(
                cast(dict[str, Any], before), "__muzilla_physical_guard_after"
            )

            def persist_replacement_checkpoint(
                checkpoint: dict[str, object], active_journal: ReviewFileJournal = journal
            ) -> None:
                current_blob = getattr(active_journal, "before_blob", None)
                current = dict(current_blob) if isinstance(current_blob, dict) else {}
                active_journal.before_blob = {
                    **current,
                    "__muzilla_publication_transition": checkpoint,
                }
                session.commit()

            restored_guard = restore_from_before_blob(
                session,
                path,
                cast(dict[str, Any], before),
                blob_store=blob_store,
                library_root=library_root,
                expected_guard=expected_guard,
                checkpoint=persist_replacement_checkpoint,
            )
            complete_before = _mark_publication_transition_complete(
                {
                    **before,
                    "__muzilla_physical_guard_restored": restored_guard,
                    "__muzilla_publication_transition": (
                        getattr(journal, "before_blob", {}) or {}
                    ).get("__muzilla_publication_transition"),
                }
            )
            for key, value in before.items():
                if key.startswith("__muzilla"):
                    continue
                if hasattr(track, key):
                    setattr(
                        track,
                        key,
                        tuple(value)
                        if isinstance(value, list) and key in ("artists", "genre", "mood")
                        else value,
                    )
            restore_catalog_art_identity(session, track, before, blob_store=blob_store)
            if "__muzilla_lyrics" in before:
                lyrics = before["__muzilla_lyrics"]
                track.has_lyrics = lyrics is not None
                track.lyrics_synced = (
                    bool(lyrics.get("synced")) if isinstance(lyrics, dict) else False
                )
            after_meta = read_track(path)
            _update_track_file_facts(track, path, tag_hash=compute_tag_hash(after_meta))
            journal.before_blob = complete_before
        elif phase == "move":
            before_path = Path(cast(str, step.get("before_path")))
            after_path = Path(cast(str, step.get("after_path")))
            if Path(track.path) != after_path:
                raise OSError(f"recovery_required: move path changed before undo: {track.path}")
            expected_before = journal_file_guard(
                cast(dict[str, Any], before), "__muzilla_physical_guard_before"
            )
            expected_after = journal_file_guard(
                cast(dict[str, Any], before), "__muzilla_physical_guard_after"
            )

            def persist_case_inverse_checkpoint(checkpoint: dict[str, object]) -> None:
                current = dict(journal.before_blob or {})
                journal.before_blob = {**current, "__muzilla_case_inverse": checkpoint}
                session.commit()

            restored_guard = move_file_with_guard(
                after_path,
                before_path,
                expected_source_guard=expected_after,
                expected_destination_guard=expected_before,
                library_root=library_root,
                checkpoint=persist_case_inverse_checkpoint,
            )
            if restored_guard != expected_before:
                raise OSError(
                    f"recovery_required: reverse move did not restore verified path: {before_path}"
                )
            track.path = str(before_path)
            track.filename = before_path.name
            after_meta = read_track(before_path)
            _update_track_file_facts(track, before_path, tag_hash=compute_tag_hash(after_meta))
            journal.before_blob = {**before, "__muzilla_physical_guard_restored": restored_guard}
        elif phase == "grouping":
            _restore_grouping(session, track, before)
        else:
            raise OSError(f"unsupported frozen Undo phase {phase!r}")

        journal.state = "rolled_back"
        journal.error = None
        completed = set(cast(list[int], execution["completed_journal_ids"]))
        file_steps = cast(list[dict[str, object]], file_entry["steps"])
        complete_file = all(
            cast(int, item["journal_id"]) in completed or item is step for item in file_steps
        )
        if phase == "grouping":
            restored_guard = None
        _set_step_checkpoint(
            run,
            manifest,
            execution,
            file_entry,
            step,
            track,
            restored_guard,
            complete_file=complete_file,
        )
        session.commit()
        if phase == "tags":
            from muzilla.changes.writer import _transition_from_before_blob

            transition = _transition_from_before_blob(
                cast(dict[str, Any], getattr(journal, "before_blob", {}))
            )
            path_value = transition.get("path") if transition is not None else None
            if (
                transition is not None
                and transition.get("phase") == "complete"
                and isinstance(path_value, str)
            ):
                finalize_publication_transition(
                    Path(path_value), transition, library_root=library_root
                )
    except Exception:
        session.rollback()
        raise


def apply_review_undo_run(
    session: Session,
    undo_run_id: int,
    *,
    library_root: Path,
    blob_store: BlobStore | None = None,
    backup_store: BackupStore | None = None,
    create_directories: bool = False,
    should_cancel: object | None = None,
) -> BundleUndoResult:
    del backup_store, create_directories
    from muzilla.db.models import ApplyRun

    begin_sqlite_write_transaction(session)
    run = session.get(ReviewUndoRun, undo_run_id)
    if run is None:
        raise BundleUndoError(f"undo run {undo_run_id} not found")
    if (
        run.state == "undone"
        and isinstance(run.result, dict)
        and run.result.get("state") == "undone"
    ):
        session.rollback()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="undone",
        )
    if run.state == "failed":
        result = run.result if isinstance(run.result, dict) else {}
        if not _is_true(result.get("retryable")) and not (
            _is_true(result.get("cancelled")) and not _is_true(result.get("recovery_required"))
        ):
            session.rollback()
            return BundleUndoResult(
                undo_run_id=run.id,
                review_bundle_id=run.review_bundle_id,
                source_apply_run_id=run.source_apply_run_id,
                state="failed",
                recovery_required=_is_true(result.get("recovery_required")),
            )
    if run.state not in {"pending", "failed"}:
        session.rollback()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
            recovery_required=True,
        )

    source_run = session.get(ApplyRun, run.source_apply_run_id)
    if source_run is None:
        run.state = "failed"
        run.error = "source apply run not found"
        run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [],
            "recovery_required": True,
            "retryable": False,
        }
        session.commit()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
            recovery_required=True,
        )
    if source_run.state not in {"applied", "partially_applied"}:
        run.state = "failed"
        run.error = f"source run not applied (state={source_run.state})"
        run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [],
            "recovery_required": False,
            "retryable": False,
        }
        session.commit()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
        )

    journals = list(
        session.scalars(
            select(ReviewFileJournal)
            .where(ReviewFileJournal.apply_run_id == source_run.id)
            .order_by(ReviewFileJournal.id.desc())
        )
    )
    expected_track_ids = _expected_track_ids(session, source_run.id)
    journal_track_ids = {journal.track_id for journal in journals}
    if expected_track_ids and not journal_track_ids.issuperset(expected_track_ids):
        missing = sorted(expected_track_ids - journal_track_ids)
        error = f"undo expired: journal retention window elapsed — missing journals for track(s) {missing}"
        run.state = "failed"
        run.error = error
        run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [],
            "recovery_required": False,
            "retryable": False,
        }
        session.commit()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
            errors={track_id: error for track_id in missing},
        )
    if expected_track_ids and not journals:
        error = "undo expired: journal retention window elapsed — no journals retained"
        run.state = "failed"
        run.error = error
        run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [],
            "recovery_required": False,
            "retryable": False,
        }
        session.commit()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
            errors={track_id: error for track_id in expected_track_ids},
        )

    manifest = run.manifest if isinstance(run.manifest, dict) else {}
    try:
        if manifest.get("version") != 2:
            manifest = _freeze_legacy_manifest(session, run, source_run.id, journals)
            run.manifest = manifest
        files = _manifest_files(manifest)
        execution = _execution(manifest)
    except Exception as exc:
        run.state = "failed"
        run.error = f"recovery_required: {exc}"
        run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [],
            "recovery_required": True,
            "retryable": False,
        }
        session.commit()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
            recovery_required=True,
        )
    if files and {cast(int, item["track_id"]) for item in files} != journal_track_ids:
        run.state = "failed"
        run.error = "recovery_required: frozen Undo file set does not match source journals"
        run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [],
            "recovery_required": True,
            "retryable": False,
        }
        session.commit()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
            recovery_required=True,
        )

    run.state = "undoing"
    session.commit()

    try:
        errors, unknown, journal_by_id = _preflight(
            session,
            run,
            source_run.id,
            journals,
            manifest,
            execution,
            library_root=library_root,
            blob_store=blob_store,
        )
    except Exception as exc:
        errors = {cast(int, item["track_id"]): str(exc) for item in files}
        unknown = True
        journal_by_id = {}
    if errors:
        retryable = not unknown
        recovery_required = (
            unknown
            or bool(execution["completed_journal_ids"])
            or any(
                error.startswith(
                    ("recovery_required:", "source drift or recovery evidence failure")
                )
                for error in errors.values()
            )
        )
        return _error_result(
            session,
            run,
            manifest,
            execution,
            errors,
            recovery_required=recovery_required,
            retryable=retryable,
        )

    for file_entry in files:
        track_id = cast(int, file_entry["track_id"])
        steps = cast(list[dict[str, object]], file_entry["steps"])
        completed = set(cast(list[int], execution["completed_journal_ids"]))
        if all(cast(int, step["journal_id"]) in completed for step in steps):
            continue
        if _cancel_requested(should_cancel):
            recovery_required = bool(completed)
            return _error_result(
                session,
                run,
                manifest,
                execution,
                {},
                cancelled=True,
                recovery_required=recovery_required,
                retryable=True,
            )
        track = session.get(Track, track_id)
        if track is None:
            return _error_result(
                session,
                run,
                manifest,
                execution,
                {track_id: f"track {track_id} not found during undo"},
                recovery_required=bool(completed),
                retryable=not bool(execution.get("active_journal_id")),
            )
        for step in steps:
            journal_id = cast(int, step["journal_id"])
            if journal_id in completed:
                continue
            journal = journal_by_id.get(journal_id)
            if journal is None:
                return _error_result(
                    session,
                    run,
                    manifest,
                    execution,
                    {track_id: "source journal disappeared during Undo"},
                    recovery_required=True,
                    retryable=False,
                )
            try:
                _apply_step(
                    session,
                    run,
                    manifest,
                    execution,
                    file_entry,
                    step,
                    journal,
                    track,
                    library_root=library_root,
                    blob_store=blob_store,
                )
                completed = set(cast(list[int], execution["completed_journal_ids"]))
            except Exception as exc:
                session.rollback()
                refreshed = session.get(ReviewUndoRun, undo_run_id)
                if refreshed is None:
                    raise BundleUndoError("Undo run disappeared while recording failure") from exc
                manifest = refreshed.manifest if isinstance(refreshed.manifest, dict) else manifest
                try:
                    execution = _execution(manifest)
                except Exception:
                    execution = {
                        "completed_journal_ids": [],
                        "active_journal_id": journal_id,
                        "step_checkpoints": {},
                        "file_checkpoints": {},
                    }
                error = f"recovery_required: {exc}"
                return _error_result(
                    session,
                    refreshed,
                    manifest,
                    execution,
                    {track_id: error},
                    recovery_required=True,
                    retryable=False,
                )

    result_files: list[dict[str, object]] = [
        {
            "track_id": cast(int, entry["track_id"]),
            "state": "undone",
            "source_change_set_ids": [],
            "error": None,
            "retryable": False,
        }
        for entry in files
    ]
    run.state = "undone"
    run.error = None
    run.result = {
        "state": "undone",
        "atomicity": "review_bundle",
        "files": result_files,
        "recovery_required": False,
        "retryable": False,
    }
    session.commit()
    return BundleUndoResult(
        undo_run_id=run.id,
        review_bundle_id=run.review_bundle_id,
        source_apply_run_id=run.source_apply_run_id,
        state="undone",
        files=tuple(
            UndoFileResult(track_id=cast(int, entry["track_id"]), state="undone") for entry in files
        ),
    )


def reconcile_interrupted_undo(session: Session, undo_run_id: int, *, library_root: Path) -> bool:
    """Reconcile one active inverse to its applied checkpoint after lock acquisition."""
    from muzilla.changes.writer import (
        _transition_from_before_blob,
        cleanup_recovered_publication_transition,
        is_case_only_path_change,
        journal_file_guard,
        move_file_with_guard,
        reconcile_case_only_move_to_source,
        recover_publication_transition,
        verify_file_guard,
    )
    from muzilla.db.models import ApplyRun

    run = session.get(ReviewUndoRun, undo_run_id)
    if run is None:
        raise BundleUndoError(f"undo run {undo_run_id} not found")
    source_run = session.get(ApplyRun, run.source_apply_run_id)
    if source_run is None:
        raise BundleUndoError("source apply run not found during Undo recovery")
    manifest = run.manifest if isinstance(run.manifest, dict) else {}
    raw_execution = manifest.get("execution", {})
    execution = raw_execution if isinstance(raw_execution, dict) else {}
    if manifest.get("version") == 2:
        if manifest.get("source_apply_run_id") != source_run.id:
            raise OSError("recovery_required: frozen Undo source run does not match")
        try:
            _manifest_files(manifest)
            execution = _execution(manifest)
        except BundleUndoError as exc:
            raise OSError("recovery_required: frozen Undo execution manifest is invalid") from exc
    active_id = execution.get("active_journal_id")
    writing = list(
        session.scalars(
            select(ReviewFileJournal).where(
                ReviewFileJournal.apply_run_id == source_run.id,
                ReviewFileJournal.state == "writing",
            )
        )
    )
    if not writing and active_id is None:
        return True
    if len(writing) != 1 or not isinstance(writing[0].id, int):
        raise OSError("recovery_required: interrupted Undo has ambiguous writing journals")
    journal = writing[0]
    if active_id is not None and active_id != journal.id:
        raise OSError("recovery_required: active Undo journal does not match writing evidence")
    step: dict[str, object] | None = None
    if manifest.get("version") == 2:
        try:
            for file_entry in _manifest_files(manifest):
                for candidate in cast(list[dict[str, object]], file_entry["steps"]):
                    if candidate.get("journal_id") == journal.id:
                        step = candidate
                        break
                if step is not None:
                    break
        except BundleUndoError as exc:
            raise OSError("recovery_required: frozen Undo manifest is invalid") from exc
        if step is None:
            raise OSError("recovery_required: active Undo journal is absent from frozen manifest")
    else:
        step = _journal_step(journal)
    track = session.get(Track, journal.track_id)
    if track is None:
        raise OSError("recovery_required: interrupted Undo track is missing")
    before = cast(dict[str, object], step["before_blob"])

    def persist_recovery_checkpoint(checkpoint: dict[str, object]) -> None:
        current_blob = dict(journal.before_blob or {})
        journal.before_blob = {
            **current_blob,
            "__muzilla_publication_transition": checkpoint,
        }
        session.commit()

    def persist_case_recovery_checkpoint(checkpoint: dict[str, object]) -> None:
        current_blob = dict(journal.before_blob or {})
        journal.before_blob = {**current_blob, "__muzilla_case_inverse": checkpoint}
        session.commit()

    phase = step.get("phase")
    if phase == "tags":
        transition = _transition_from_before_blob(cast(dict[str, Any], journal.before_blob or {}))
        transition_path = transition.get("path") if transition is not None else None
        if transition is not None and isinstance(transition_path, str):
            recovered = recover_publication_transition(
                Path(transition_path),
                transition,
                library_root=library_root,
                checkpoint=persist_recovery_checkpoint,
            )
            journal.before_blob = {
                **dict(journal.before_blob or {}),
                "__muzilla_publication_transition": recovered,
            }
            cleanup_recovered_publication_transition(
                Path(transition_path), recovered, library_root=library_root
            )
        expected = journal_file_guard(
            cast(dict[str, Any], before), "__muzilla_physical_guard_after"
        )
        verify_file_guard(Path(track.path), expected, library_root)
    elif phase == "move":
        before_path = Path(cast(str, step.get("before_path")))
        after_path = Path(cast(str, step.get("after_path")))
        expected_before = journal_file_guard(
            cast(dict[str, Any], before), "__muzilla_physical_guard_before"
        )
        expected_after = journal_file_guard(
            cast(dict[str, Any], before), "__muzilla_physical_guard_after"
        )
        if is_case_only_path_change(after_path, before_path):
            journal_blob = journal.before_blob if isinstance(journal.before_blob, dict) else {}
            checkpoint = journal_blob.get("__muzilla_case_inverse")
            intermediate = (
                Path(cast(str, checkpoint["intermediate"]))
                if isinstance(checkpoint, dict) and isinstance(checkpoint.get("intermediate"), str)
                else None
            )
            reconcile_case_only_move_to_source(
                after_path,
                before_path,
                expected_source_guard=expected_after,
                library_root=library_root,
                intermediate=intermediate,
                checkpoint=persist_case_recovery_checkpoint,
            )
        else:
            try:
                verify_file_guard(after_path, expected_after, library_root)
            except OSError as err:
                verify_file_guard(before_path, expected_before, library_root)
                moved_guard = move_file_with_guard(
                    before_path,
                    after_path,
                    expected_source_guard=expected_before,
                    expected_destination_guard=expected_after,
                    library_root=library_root,
                )
                if moved_guard != expected_after:
                    raise OSError(
                        "recovery_required: interrupted move could not return to applied path"
                    ) from err
        verify_file_guard(after_path, expected_after, library_root)
    elif phase == "grouping":
        if not _group_applied_matches(session, track, before):
            raise OSError("recovery_required: grouping inverse outcome is uncertain")
    else:
        raise OSError("recovery_required: interrupted Undo phase is unsupported")

    journal.state = "done"
    journal.error = None
    if manifest.get("version") == 2:
        updated_execution = dict(execution)
        updated_execution["active_journal_id"] = None
        _set_execution(run, manifest, updated_execution)
    else:
        run.manifest = _freeze_legacy_manifest(
            session,
            run,
            source_run.id,
            list(
                session.scalars(
                    select(ReviewFileJournal)
                    .where(ReviewFileJournal.apply_run_id == source_run.id)
                    .order_by(ReviewFileJournal.id.desc())
                )
            ),
        )
    session.commit()
    return True


def interrupted_undo_file_results(
    session: Session, undo_run_id: int, *, library_root: Path
) -> tuple[list[dict[str, object]], bool]:
    """Rebuild per-file recovery outcomes from verified current checkpoints."""
    from muzilla.db.models import ApplyRun

    run = session.get(ReviewUndoRun, undo_run_id)
    if run is None:
        raise BundleUndoError(f"undo run {undo_run_id} not found")
    source_run = session.get(ApplyRun, run.source_apply_run_id)
    if source_run is None:
        raise BundleUndoError("source apply run not found during Undo recovery")
    journals = list(
        session.scalars(
            select(ReviewFileJournal)
            .where(ReviewFileJournal.apply_run_id == source_run.id)
            .order_by(ReviewFileJournal.id.desc())
        )
    )
    manifest = run.manifest if isinstance(run.manifest, dict) else {}
    if manifest.get("version") != 2:
        manifest = _freeze_legacy_manifest(session, run, source_run.id, journals)
        run.manifest = manifest
    files = _manifest_files(manifest)
    execution = _execution(manifest)
    errors, unknown, _ = _preflight(
        session,
        run,
        source_run.id,
        journals,
        manifest,
        execution,
        library_root=library_root,
        blob_store=None,
        validate_blobs=False,
        check_conflicts=False,
    )
    completed = set(cast(list[int], execution["completed_journal_ids"]))
    file_results: list[dict[str, object]] = []
    for file_entry in files:
        track_id = cast(int, file_entry["track_id"])
        steps = cast(list[dict[str, object]], file_entry["steps"])
        file_complete = all(cast(int, step["journal_id"]) in completed for step in steps)
        error = errors.get(track_id)
        if file_complete and error is None:
            file_results.append(
                {
                    "track_id": track_id,
                    "state": "undone",
                    "source_change_set_ids": [],
                    "error": None,
                    "retryable": False,
                }
            )
        else:
            file_results.append(
                {
                    "track_id": track_id,
                    "state": "failed",
                    "source_change_set_ids": [],
                    "error": error
                    or "recovery_required: interrupted Undo did not complete this file",
                    "retryable": not unknown,
                }
            )
    return file_results, not unknown
