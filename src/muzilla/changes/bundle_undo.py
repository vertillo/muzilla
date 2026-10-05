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


def _manifest_file_retryable(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    retryable = entry.get("retryable")
    return isinstance(retryable, bool) and retryable


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
            recovery_required=(
                bool(run.result.get("recovery_required")) if isinstance(run.result, dict) else False
            ),
        )
    # failed but retryable should be executable: check manifest retryable flag or cancelled without recovery_required
    if run.state == "failed" and run.result is not None:
        is_retryable = False
        if isinstance(run.result, dict):
            # legacy: check manifest files retryable or result recovery flag
            _raw = run.manifest.get("files", []) if isinstance(run.manifest, dict) else []
            _files = _raw if isinstance(_raw, list) else []
            if isinstance(_files, list):
                is_retryable = any(_manifest_file_retryable(entry) for entry in _files)
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
                recovery_required=(
                    bool(run.result.get("recovery_required"))
                    if isinstance(run.result, dict)
                    else False
                ),
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
            recovery_required=True,
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
    preflight_requires_recovery = False
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
                preflight_errors[tid] = f"recovery_required: file missing for undo: {cur_path}"
                preflight_requires_recovery = True
                break
            if cur_path.is_symlink():
                preflight_errors[tid] = (
                    f"recovery_required: refusing to follow symlink for undo: {cur_path}"
                )
                preflight_requires_recovery = True
                break
        if tid not in preflight_errors:
            physical_journals = [
                journal
                for journal in journals
                if journal.track_id == tid
                and journal.state == "done"
                and journal.phase in {"tags", "move"}
            ]
            if physical_journals:
                from muzilla.changes.writer import journal_file_guard, verify_file_guard

                try:
                    expected_guard: dict[str, object] | None = None
                    current_path = str(Path(track.path).absolute())
                    for journal in physical_journals:
                        if journal.phase == "move" and journal.after_path == track.path:
                            expected_guard = journal_file_guard(
                                journal.before_blob or {}, "__muzilla_physical_guard_after"
                            )
                            break
                        tag_guard = (journal.before_blob or {}).get(
                            "__muzilla_physical_guard_after"
                        )
                        if (
                            journal.phase == "tags"
                            and isinstance(tag_guard, dict)
                            and tag_guard.get("path") == current_path
                        ):
                            expected_guard = journal_file_guard(
                                journal.before_blob or {}, "__muzilla_physical_guard_after"
                            )
                            break
                    if expected_guard is None:
                        raise OSError("persisted physical restoration evidence is unavailable")
                    verify_file_guard(Path(track.path), expected_guard, library_root)
                except Exception as exc:
                    preflight_errors[tid] = f"recovery_required: physical drift before undo: {exc}"
                    preflight_requires_recovery = True
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
            "recovery_required": preflight_requires_recovery,
        }
        session.commit()
        return BundleUndoResult(
            undo_run_id=run.id,
            review_bundle_id=run.review_bundle_id,
            source_apply_run_id=run.source_apply_run_id,
            state="failed",
            errors=dict(preflight_errors),
            recovery_required=preflight_requires_recovery,
        )
    # Attempt to restore each journal in reverse order
    from pathlib import Path as _Path

    from muzilla.changes.writer import (
        _mark_publication_transition_complete,
        _transition_from_before_blob,
        _update_track_file_facts,
        finalize_publication_transition,
        is_case_only_path_change,
        journal_file_guard,
        move_file_with_guard,
        restore_catalog_art_identity,
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
            # A cancellation with no restored files is retryable. Once this run
            # has restored only part of the bundle, it fails closed: the bundle
            # cannot claim completion or offer an unproven retry as restored.
            has_writing = any(j.state == "writing" for j in journals)
            has_remaining_files = any(j.state == "done" for j in journals)
            recovery_required = has_writing or (bool(undone_ids) and has_remaining_files)
            run.state = "failed"
            run.error = (
                "recovery_required: cancellation interrupted partial Undo"
                if recovery_required
                else "cancelled during undo"
            )
            run.result = {
                "state": "failed",
                "atomicity": "review_bundle",
                "cancelled": True,
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
                recovery_required=recovery_required,
            )
        if journal.state not in {"done", "rolled_back"}:
            continue
        track = session.get(Track, journal.track_id)
        if track is None:
            errors[journal.track_id] = "track not found during undo"
            seen[journal.track_id] = "failed"
            continue
        # Persist an in-progress marker before touching the file, then release the
        # SQLite writer lock while the synchronous restoration runs. A crash in
        # this interval remains fail-closed because recovery can see `writing`.
        journal.state = "writing"
        session.commit()
        try:
            if journal.phase == "tags":
                cur_path = _Path(track.path)
                if not cur_path.exists():
                    cur_path = _Path(journal.path)
                if not cur_path.exists():
                    raise OSError(f"file missing for undo tags: {cur_path}")
                before = journal.before_blob or {}
                expected_guard = journal_file_guard(before, "__muzilla_physical_guard_after")

                def persist_replacement_checkpoint(
                    checkpoint: dict[str, object],
                    active_journal: ReviewFileJournal = journal,
                ) -> None:
                    active_journal.before_blob = {
                        **dict(active_journal.before_blob or {}),
                        "__muzilla_publication_transition": checkpoint,
                    }
                    session.commit()

                restored_guard = restore_from_before_blob(
                    session,
                    cur_path,
                    before,
                    blob_store=blob_store,
                    library_root=library_root,
                    expected_guard=expected_guard,
                    checkpoint=persist_replacement_checkpoint,
                )
                journal.before_blob = _mark_publication_transition_complete(
                    {
                        **before,
                        "__muzilla_physical_guard_restored": restored_guard,
                        "__muzilla_publication_transition": (
                            (journal.before_blob or {}).get("__muzilla_publication_transition")
                        ),
                    }
                )
                # update track facts
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
                restore_catalog_art_identity(session, track, before, blob_store=blob_store)
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
                before_blob = journal.before_blob or {}
                expected_before = journal_file_guard(before_blob, "__muzilla_physical_guard_before")
                expected_after = journal_file_guard(before_blob, "__muzilla_physical_guard_after")
                if Path(track.path) != after_path:
                    raise OSError(f"recovery_required: move path changed before undo: {track.path}")
                case_only = is_case_only_path_change(after_path, before_path)

                def persist_case_inverse(
                    checkpoint: dict[str, object],
                    active_journal: ReviewFileJournal = journal,
                ) -> None:
                    active_journal.before_blob = {
                        **dict(active_journal.before_blob or {}),
                        "__muzilla_case_inverse": checkpoint,
                    }
                    session.commit()

                restored_guard = move_file_with_guard(
                    after_path,
                    before_path,
                    expected_source_guard=expected_after,
                    expected_destination_guard=expected_before,
                    library_root=library_root,
                    checkpoint=persist_case_inverse if case_only else None,
                )
                if restored_guard != expected_before:
                    raise OSError(
                        f"recovery_required: reverse move did not restore verified path: {before_path}"
                    )
                tag_journal = session.scalar(
                    _select(ReviewFileJournal)
                    .where(
                        ReviewFileJournal.apply_run_id == source_run.id,
                        ReviewFileJournal.track_id == journal.track_id,
                        ReviewFileJournal.phase == "tags",
                    )
                    .order_by(ReviewFileJournal.id.desc())
                    .limit(1)
                )
                if tag_journal is not None:
                    tag_before = tag_journal.before_blob or {}
                    expected_tags = journal_file_guard(tag_before, "__muzilla_physical_guard_after")
                    if restored_guard != expected_tags:
                        raise OSError(
                            "recovery_required: reverse move does not match tag restore evidence"
                        )
                    tag_journal.before_blob = {
                        **tag_before,
                        "__muzilla_physical_guard_after": restored_guard,
                    }
                journal.before_blob = {
                    **before_blob,
                    "__muzilla_physical_guard_restored": restored_guard,
                }
                track.path = str(before_path)
                track.filename = before_path.name
                after_meta = read_track(before_path)
                _update_track_file_facts(track, before_path, tag_hash=compute_tag_hash(after_meta))
                journal.state = "rolled_back"
                seen[journal.track_id] = "undone"
                undone_ids.append(journal.track_id)
            session.flush()
            # Each completed file is a durable safe boundary. In particular,
            # do not hold SQLite's writer lock during the next file restore.
            session.commit()
            transition = _transition_from_before_blob(dict(journal.before_blob or {}))
            transition_path = transition.get("path") if transition is not None else None
            if (
                transition is not None
                and transition.get("phase") == "complete"
                and isinstance(transition_path, str)
            ):
                finalize_publication_transition(
                    _Path(transition_path), transition, library_root=library_root
                )
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
            recovery_required=True,
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


def recover_interrupted_case_undo(
    session: Session, undo_run_id: int, *, library_root: Path
) -> bool:
    """Return interrupted case-only Undo moves to their applied spelling."""
    from sqlalchemy import select

    from muzilla.changes.writer import (
        _transition_from_before_blob,
        cleanup_recovered_publication_transition,
        finalize_publication_transition,
        is_case_only_path_change,
        journal_file_guard,
        reconcile_case_only_move_to_source,
        recover_publication_transition,
    )
    from muzilla.db.models import ApplyRun, ReviewFileJournal, ReviewUndoRun, Track

    undo_run = session.get(ReviewUndoRun, undo_run_id)
    if undo_run is None:
        raise BundleUndoError(f"undo run {undo_run_id} not found")
    source_run = session.get(ApplyRun, undo_run.source_apply_run_id)
    if source_run is None:
        raise BundleUndoError("source apply run not found during case-only Undo recovery")
    journals = list(
        session.scalars(
            select(ReviewFileJournal).where(
                ReviewFileJournal.apply_run_id == source_run.id,
                ReviewFileJournal.state == "writing",
                ReviewFileJournal.phase.in_(["move", "tags"]),
            )
        )
    )
    recovered = False
    for journal in journals:
        if journal.phase == "tags":
            before_blob = dict(journal.before_blob or {})
            transition = _transition_from_before_blob(before_blob)
            if transition is None or transition.get("purpose") != "restore":
                continue
            if transition.get("phase") == "complete":
                transition_path = transition.get("path")
                if isinstance(transition_path, str):
                    finalize_publication_transition(
                        Path(transition_path), transition, library_root=library_root
                    )
                continue
            transition_path = transition.get("path")
            if not isinstance(transition_path, str):
                raise OSError("interrupted Undo replacement is missing its source path")

            def persist_publication_recovery(
                checkpoint: dict[str, object],
                active_journal: ReviewFileJournal = journal,
            ) -> None:
                active_journal.before_blob = {
                    **dict(active_journal.before_blob or {}),
                    "__muzilla_publication_transition": checkpoint,
                }
                session.commit()

            recovered_transition = recover_publication_transition(
                Path(transition_path),
                transition,
                library_root=library_root,
                checkpoint=persist_publication_recovery,
            )
            journal.before_blob = {
                **before_blob,
                "__muzilla_publication_transition": recovered_transition,
            }
            journal.state = "done"
            journal.error = None
            session.commit()
            cleanup_recovered_publication_transition(
                Path(transition_path), recovered_transition, library_root=library_root
            )
            recovered = True
            continue
        before_path = Path(journal.before_path) if journal.before_path else None
        after_path = Path(journal.after_path) if journal.after_path else None
        if (
            before_path is None
            or after_path is None
            or not is_case_only_path_change(after_path, before_path)
        ):
            continue
        before_blob = journal.before_blob or {}
        expected_after = journal_file_guard(before_blob, "__muzilla_physical_guard_after")
        checkpoint_path: Path | None = None
        for key in (
            "__muzilla_case_undo_recovery",
            "__muzilla_case_inverse",
            "__muzilla_case_recovery",
        ):
            checkpoint = before_blob.get(key)
            if isinstance(checkpoint, dict) and isinstance(checkpoint.get("intermediate"), str):
                checkpoint_path = Path(checkpoint["intermediate"])
                break

        def persist_case_recovery(
            checkpoint: dict[str, object],
            active_journal: ReviewFileJournal = journal,
        ) -> None:
            active_journal.before_blob = {
                **dict(active_journal.before_blob or {}),
                "__muzilla_case_undo_recovery": checkpoint,
            }
            session.commit()

        restored = reconcile_case_only_move_to_source(
            after_path,
            before_path,
            expected_source_guard=expected_after,
            library_root=library_root,
            intermediate=checkpoint_path,
            checkpoint=persist_case_recovery,
        )
        if restored != expected_after:
            raise OSError("interrupted case-only Undo did not return to its applied path")
        track = session.get(Track, journal.track_id)
        if track is None or Path(track.path) != after_path:
            raise OSError("catalog path changed during interrupted case-only Undo")
        journal.before_blob = {
            **before_blob,
            "__muzilla_case_undo_recovery_restored": True,
        }
        journal.state = "done"
        journal.error = None
        session.commit()
        recovered = True
    return recovered
