"""Persistent per-file execution of a frozen ReviewBundle undo run.

# ponytail: native undo via inverting ReviewFileJournal; minimal restore via writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

from sqlalchemy.orm import Session  # pyright: ignore[reportMissingImports]

from muzilla.changes.backup import BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.db.models import ReviewUndoRun


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
    from sqlalchemy import select as _select  # pyright: ignore[reportMissingImports]

    from muzilla.db.models import ApplyRun, ReviewFileJournal, Track

    run = session.get(ReviewUndoRun, undo_run_id)
    if run is None:
        raise BundleUndoError(f"undo run {undo_run_id} not found")
    # idempotent if already undone
    if run.state == "undone" and run.result is not None:
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state=run.state,
        )
    # failed but retryable should be executable: check manifest retryable flag or cancelled without recovery_required
    if run.state == "failed" and run.result is not None:
        is_retryable = False
        if isinstance(run.result, dict):
            # legacy: check manifest files retryable or result recovery flag
            _raw = run.manifest.get("files", []) if isinstance(run.manifest, dict) else []
            _files = _raw if isinstance(_raw, list) else []
            if isinstance(_files, list):
                is_retryable = any(
                    isinstance(e, dict) and e.get("retryable") is True for e in _files
                )
            # cancelled without recovery_required is retryable even without manifest flag
            if run.result.get("cancelled") and not bool(run.result.get("recovery_required")):
                is_retryable = True
            # if recovery_required True, not retryable until recovered
            if bool(run.result.get("recovery_required")):
                is_retryable = False
        if not is_retryable:
            return BundleUndoResult(
                undo_run_id=run.id,
                review_bundle_id=run.review_bundle_id,
                source_apply_run_id=run.source_apply_run_id,
                state=run.state,
            )
        # retry: reset to pending for re-execution
        run.state = "pending"
        run.error = None
        run.result = None
        session.flush()
    source_run = session.get(ApplyRun, run.source_apply_run_id)
    if source_run is None:
        run.state = "failed"
        run.error = "source apply run not found"
        run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [],
            "recovery_required": True,
        }
        session.commit()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
        )
    # Must have journals; if none, nothing to undo but still succeed idempotently
    journals = list(
        session.scalars(
            _select(ReviewFileJournal)
            .where(ReviewFileJournal.apply_run_id == source_run.id)
            .order_by(ReviewFileJournal.id.desc())
        )
    )
    # check source was applied
    if source_run.state != "applied":
        run.state = "failed"
        run.error = f"source run not applied (state={source_run.state})"
        run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [],
            "recovery_required": False,
        }
        session.commit()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
        )
    # Retention expiry: fail closed if journals were pruned (age/count threshold)
    # Journals are the raw material for undo; if they were removed by retention sweep,
    # undo must not succeed spuriously with an empty file set. Preserve recovery states
    # (those journals are never pruned), so this only fires for truly expired runs.
    from muzilla.db.models import Operation as _Op  # local to avoid cycle
    from muzilla.db.models import OperationAttempt as _OpAttempt

    _expected_tids: set[int] = set()
    for _att in session.scalars(
        _select(_OpAttempt).where(
            _OpAttempt.apply_run_id == source_run.id,
            _OpAttempt.state == "applied",
        )
    ):
        _op = session.get(_Op, _att.operation_id)
        if _op is not None and _op.target_type == "track":
            _expected_tids.add(_op.target_id)
    # Fallback to manifest if operation_attempts not yet flushed/legacy
    if not _expected_tids:
        _manifest = run.manifest if isinstance(run.manifest, dict) else {}
        _raw_files = _manifest.get("files", [])
        if isinstance(_raw_files, list):
            for _e in _raw_files:
                if isinstance(_e, dict) and isinstance(_e.get("track_id"), int):
                    _expected_tids.add(int(_e["track_id"]))
    _journal_tids = {j.track_id for j in journals}
    if _expected_tids and not _journal_tids.issuperset(_expected_tids):
        _missing = sorted(_expected_tids - _journal_tids)
        _msg = (
            f"undo expired: journal retention window elapsed (age/count threshold) — "
            f"missing journals for track(s) {_missing}"
            if _journal_tids
            else "undo expired: journal retention window elapsed (age/count threshold) — no journals retained"
        )
        run.state = "failed"
        run.error = _msg
        run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [
                {
                    "track_id": tid,
                    "state": "failed",
                    "source_change_set_ids": [],
                    "error": _msg,
                    "retryable": False,
                }
                for tid in _expected_tids
            ],
            "recovery_required": False,
        }
        session.commit()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
        )
    # Bundle-level preflight before any mutation: validate frozen manifest, track existence,
    # concurrent applies, and source drift (fail-closed: do not clobber externally edited files).
    preflight_errors: dict[int, str] = {}
    # check manifest files exist and tracks present, no concurrent apply
    manifest = run.manifest if isinstance(run.manifest, dict) else {}
    raw_files = manifest.get("files", [])
    preflight_files: list[dict[str, object]] = (
        [e for e in raw_files if isinstance(e, dict)] if isinstance(raw_files, list) else []
    )
    # Build track set for undo (from journals, fallback to manifest)
    _undo_tids_for_preflight: set[int] = set()
    for j in journals:
        if j.state in {"done", "rolled_back"}:
            _undo_tids_for_preflight.add(j.track_id)
    for entry in preflight_files:
        tid = entry.get("track_id")
        if isinstance(tid, int):
            _undo_tids_for_preflight.add(tid)
    # check each file's journal preconditions + drift vs current file state
    for tid in _undo_tids_for_preflight:
        track = session.get(Track, tid)
        if track is None:
            preflight_errors[tid] = f"track {tid} not found for undo"
            continue
        # Drift check: current file must still match the "after" state recorded in journal
        # Use tag_hash comparison (authoritative) and file existence; if drift, block whole undo.
        # ponytail: minimal tag_hash drift guard; writer._source_precondition_error handles stat edge,
        # but for undo we only have after_hash, so compare directly.
        for j in journals:
            if j.track_id != tid or j.state not in {"done", "rolled_back"}:
                continue
            # For tags phase, after_hash is the hash after apply; current track.tag_hash must match
            if j.phase == "tags" and j.after_hash is not None and track.tag_hash != j.after_hash:
                preflight_errors[tid] = (
                    f"source drift detected for track {tid}: tag hash changed after apply"
                )
                break
            # For move phase, current path must still be after_path
            if j.phase == "move" and j.after_path is not None and track.path != j.after_path:
                preflight_errors[tid] = (
                    f"source drift detected for track {tid}: path changed after apply (expected {j.after_path!r}, got {track.path!r})"
                )
                break
            # Existence and guard check
            cur_path = Path(track.path)
            if not cur_path.exists():
                preflight_errors[tid] = f"file missing for undo: {cur_path}"
                break
            if cur_path.is_symlink():
                preflight_errors[tid] = f"refusing to follow symlink for undo: {cur_path}"
                break
    # concurrent apply check: any other ApplyRun pending/applying targeting same track
    if not preflight_errors:
        undo_tids: set[int] = set()
        for _e in preflight_files:
            _tid = _e.get("track_id")
            if isinstance(_tid, int):
                undo_tids.add(_tid)
        if undo_tids:
            other_runs = list(
                session.scalars(
                    _select(ApplyRun).where(
                        ApplyRun.id != source_run.id, ApplyRun.state.in_(["pending", "applying"])
                    )
                )
            )
            for other in other_runs:
                try:
                    other_files = (
                        other.manifest.get("files", []) if isinstance(other.manifest, dict) else []
                    )
                    if not isinstance(other_files, list):
                        continue
                    other_ids: set[int] = set()
                    for _f in other_files:
                        if isinstance(_f, dict) and isinstance(_f.get("track_id"), int):
                            other_ids.add(_f.get("track_id"))  # type: ignore[arg-type]
                except Exception:
                    continue
                overlap = undo_tids & other_ids
                if overlap:
                    for tid in overlap:
                        preflight_errors[tid] = (
                            f"concurrent review targets same file(s): {sorted(overlap)}"
                        )
                    break
    if preflight_errors:
        first = next(iter(preflight_errors.values()))
        run.state = "failed"
        run.error = first
        run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [
                {
                    "track_id": tid,
                    "state": "failed",
                    "source_change_set_ids": [],
                    "error": err,
                    "retryable": False,
                }
                for tid, err in preflight_errors.items()
            ],
            "recovery_required": False,
        }
        session.commit()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
            errors=dict(preflight_errors),
        )
    # Attempt to restore each journal in reverse order
    from pathlib import Path as _Path

    from muzilla.changes.writer import (
        _fsync_directory,
        _move_no_clobber,
        _update_track_file_facts,
        restore_from_before_blob,
    )
    from muzilla.domain.metadata import tag_hash as compute_tag_hash
    from muzilla.tags.reader import read_track

    # cancellation check helper
    def _should_cancel() -> bool:
        if should_cancel is None:
            return False
        try:
            return bool(should_cancel()) if callable(should_cancel) else bool(should_cancel)
        except Exception:
            return False

    run.state = "undoing"
    session.commit()
    errors: dict[int, str] = {}
    # track ids for result
    seen: dict[int, str] = {}
    undone_ids: list[int] = []
    for journal in journals:
        if _should_cancel():
            # cancellation is atomic: only mark recovery_required if a journal is still in writing/partial state.
            # If all undone journals were cleanly rolled back and no writing remains, it is retryable.
            has_writing = any(j.state == "writing" for j in journals)
            # also consider any journal that was not yet rolled_back but was in pending/done that we interrupted
            # if we have undone_ids, those were rolled_back, so safe; only writing makes it recovery_required
            recovery_required = has_writing
            run.state = "failed"
            run.error = "cancelled during undo"
            run.result = {
                "state": "failed",
                "atomicity": "review_bundle",
                "files": [
                    {
                        "track_id": tid,
                        "state": "undone",
                        "source_change_set_ids": [],
                        "error": None,
                        "retryable": False,
                    }
                    for tid in undone_ids
                ],
                "recovery_required": recovery_required,
            }
            session.commit()
            return BundleUndoResult(
                undo_run_id=run.id,
                review_bundle_id=run.review_bundle_id,
                source_apply_run_id=run.source_apply_run_id,
                state="failed",
                cancelled=True,
            )
        if journal.state not in {"done", "rolled_back"}:
            continue
        track = session.get(Track, journal.track_id)
        if track is None:
            errors[journal.track_id] = "track not found during undo"
            seen[journal.track_id] = "failed"
            continue
        try:
            if journal.phase == "tags":
                cur_path = _Path(track.path)
                if not cur_path.exists():
                    cur_path = _Path(journal.path)
                if not cur_path.exists():
                    raise OSError(f"file missing for undo tags: {cur_path}")
                restore_from_before_blob(
                    session, cur_path, journal.before_blob, blob_store=blob_store
                )
                # update track facts
                before = journal.before_blob or {}
                for k, v in before.items():
                    if k.startswith("__muzilla"):
                        continue
                    if hasattr(track, k):
                        setattr(
                            track,
                            k,
                            tuple(v)
                            if isinstance(v, list) and k in ("artists", "genre", "mood")
                            else v,
                        )
                if "__muzilla_art_blob_id" in before:
                    val = before["__muzilla_art_blob_id"]
                    track.art_blob_id = val  # type: ignore[assignment]
                    track.has_embedded_art = val is not None
                if "__muzilla_lyrics" in before:
                    lyrics = before["__muzilla_lyrics"]
                    if lyrics is None:
                        track.has_lyrics = False
                        track.lyrics_synced = False
                    else:
                        track.has_lyrics = True
                        try:
                            track.lyrics_synced = (
                                bool(lyrics.get("synced")) if isinstance(lyrics, dict) else False
                            )
                        except Exception:
                            track.lyrics_synced = False
                after_meta = read_track(cur_path)
                after_hash = compute_tag_hash(after_meta)
                _update_track_file_facts(track, cur_path, tag_hash=after_hash)
                journal.state = "rolled_back"
                seen[journal.track_id] = "undone"
                undone_ids.append(journal.track_id)
            elif journal.phase == "grouping":
                # undo grouping is same as apply rollback: restore before group
                before = journal.before_blob or {}
                prev_group_id = before.get("group_id")
                source_group_id = before.get("source_group_id")
                source_is_pinned = before.get("source_is_pinned")
                target_group_id = before.get("target_group_id")
                target_is_pinned = before.get("target_is_pinned")
                if isinstance(prev_group_id, int) and isinstance(source_group_id, int):
                    track.work_unit_id = prev_group_id
                    from muzilla.db.models import WorkUnit as _TG

                    src = session.get(_TG, source_group_id)
                    if src is not None and isinstance(source_is_pinned, bool):
                        src.is_pinned = source_is_pinned
                    if isinstance(target_group_id, int) and isinstance(target_is_pinned, bool):
                        tgt = session.get(_TG, target_group_id)
                        if tgt is not None:
                            tgt.is_pinned = target_is_pinned
                    # recount
                    from muzilla.changes.bundle_applier import _recount_groups

                    affected = {source_group_id, prev_group_id}
                    if isinstance(target_group_id, int):
                        affected.add(target_group_id)
                    _recount_groups(session, affected)
                journal.state = "rolled_back"
                seen[journal.track_id] = "undone"
                undone_ids.append(journal.track_id)
            elif journal.phase == "move":
                before_path = _Path(journal.before_path) if journal.before_path else None
                after_path = _Path(journal.after_path) if journal.after_path else None
                if before_path is None or after_path is None:
                    raise OSError("move journal missing before/after path")
                if not after_path.exists():
                    if before_path.exists():
                        journal.state = "rolled_back"
                        seen[journal.track_id] = "undone"
                        continue
                    raise OSError(f"file missing for undo move: {after_path}")
                if before_path.exists():
                    try:
                        if before_path.samefile(after_path):
                            journal.state = "rolled_back"
                            seen[journal.track_id] = "undone"
                            continue
                    except OSError:
                        pass
                    raise OSError(f"undo destination already exists: {before_path}")
                before_path.parent.mkdir(parents=True, exist_ok=True)
                _move_no_clobber(after_path, before_path, same_file=False)
                _fsync_directory(before_path.parent)
                if after_path.parent != before_path.parent:
                    _fsync_directory(after_path.parent)
                track.path = str(before_path)
                track.filename = before_path.name
                after_meta = read_track(before_path)
                _update_track_file_facts(track, before_path, tag_hash=compute_tag_hash(after_meta))
                journal.state = "rolled_back"
                seen[journal.track_id] = "undone"
                undone_ids.append(journal.track_id)
            session.flush()
        except Exception as exc:
            errors[journal.track_id] = str(exc)
            seen[journal.track_id] = "failed"
            journal.error = str(exc)
            session.commit()
    session.commit()
    if errors:
        run.state = "failed"
        run.error = "; ".join(f"track {tid}: {e}" for tid, e in errors.items())
        run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [
                {
                    "track_id": tid,
                    "state": st,
                    "source_change_set_ids": [],
                    "error": errors.get(tid),
                    "retryable": st == "failed",
                }
                for tid, st in seen.items()
            ],
            "recovery_required": True,
        }
        session.commit()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
            errors=errors,
        )
    run.state = "undone"
    run.error = None
    run.result = {
        "state": "undone",
        "atomicity": "review_bundle",
        "files": [
            {
                "track_id": tid,
                "state": st,
                "source_change_set_ids": [],
                "error": None,
                "retryable": False,
            }
            for tid, st in seen.items()
        ],
    }
    session.commit()
    return BundleUndoResult(
        undo_run_id=run.id,
        review_bundle_id=run.review_bundle_id,
        source_apply_run_id=run.source_apply_run_id,
        state="undone",
    )
