"""Controlled atomic execution of a frozen ReviewBundle ApplyRun.

# ponytail: native ReviewBundle apply via writer primitives; journal is
# ReviewFileJournal. Bundle-level preflight validates entire bundle before
# any mutation so validation errors write nothing. Runtime failures and
# cancellations roll back via deterministic journal recovery before terminal
# failure. Never presents partially_applied as success.
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
    ProposalRevision,
    ReviewBundle,
    ReviewFileJournal,
    ReviewInboxEntry,
    Track,
    WorkUnit,
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
    recovery_required: bool = False


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
        path=path, size_bytes=size_bytes, mtime_ns=mtime_ns, tag_hash=tag_hash
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


def _file_result(
    entry: dict[str, object], attempts: dict[int, OperationAttempt]
) -> FileApplyResult:
    operation_ids = [
        op_id
        for op_id in cast(list[object], entry.get("operation_ids", []))
        if isinstance(op_id, int)
    ]
    # For rolled_back we keep state as reported in entry; applied_operation_ids only for applied
    applied_ids = tuple(
        op_id
        for op_id in operation_ids
        if attempts.get(op_id) is not None and attempts[op_id].state == "applied"
    )
    return FileApplyResult(
        track_id=cast(int, entry["track_id"]),
        state=str(entry.get("state", "failed")),
        applied_operation_ids=applied_ids,
        error=cast(str | None, entry.get("error")),
    )


def _collection_identity(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _recount_groups(session: Session, group_ids: set[int]) -> None:
    for group_id in group_ids:
        group = session.get(WorkUnit, group_id)
        if group is None:
            continue
        group.track_count = sum(
            1 for _ in session.scalars(select(Track.id).where(Track.work_unit_id == group_id))
        )


def _apply_grouping_correction(
    session: Session,
    track: Track,
    operations: list[Operation],
    attempts: dict[int, OperationAttempt],
    *,
    apply_run_id: int,
) -> tuple[str, str | None]:
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
        or track.work_unit_id != source_group_id
    ):
        return "failed", "collection changed after preview; refresh the review"
    source = session.get(WorkUnit, source_group_id)
    if source is None or source.is_pinned:
        return "failed", "collection changed after preview; refresh the review"
    # journal before mutation for atomic rollback
    before_blob: dict[str, object] = {
        "group_id": track.work_unit_id,
        "source_group_id": source_group_id,
        "source_is_pinned": source.is_pinned,
        "action": action,
    }
    journal = ReviewFileJournal(
        apply_run_id=apply_run_id,
        track_id=track.id,
        path=track.path,
        phase="grouping",
        state="pending",
        before_blob=before_blob,
        before_hash=None,
        after_hash=None,
    )
    session.add(journal)
    journal.state = "writing"
    session.flush()
    dirty_groups = {source.id}
    target_was_new = False
    try:
        if action == "confirm_collection":
            source.is_pinned = True
        elif action == "treat_as_singleton":
            singleton_key = value.get("singleton_key")
            expected_key = blake2b(f"resolver-singleton:{track.id}".encode()).hexdigest()[:32]
            if singleton_key != expected_key:
                journal.state = "failed"
                journal.error = "singleton correction key is invalid"
                session.flush()
                return "failed", "singleton correction key is invalid"
            target = session.scalar(select(WorkUnit).where(WorkUnit.key == singleton_key))
            if target is None:
                target = WorkUnit(
                    key=singleton_key,
                    kind="singleton",
                    grouping_basis="manual",
                    grouping_confidence=1.0,
                    track_count=0,
                )
                session.add(target)
                session.flush()
                target_was_new = True
            if target.kind != "singleton" or target.grouping_basis != "manual":
                journal.state = "failed"
                journal.error = "singleton correction target is invalid"
                session.flush()
                return "failed", "singleton correction target is invalid"
            before_blob["target_group_id"] = target.id
            before_blob["target_is_pinned"] = target.is_pinned
            before_blob["target_was_new"] = target_was_new
            track.work_unit_id = target.id
            target.is_pinned = True
            dirty_groups.add(target.id)
        elif action == "move_to_collection":
            target_id = value.get("to_group_id")
            if not isinstance(target_id, int) or target_id == source.id:
                journal.state = "failed"
                journal.error = "collection correction target is invalid"
                session.flush()
                return "failed", "collection correction target is invalid"
            target = session.get(WorkUnit, target_id)
            track_artist = _collection_identity(track.album_artist or track.artist)
            if (
                target is None
                or not _collection_identity(track.album)
                or not track_artist
                or _collection_identity(track.album) != _collection_identity(target.album)
                or track_artist != _collection_identity(target.album_artist)
            ):
                journal.state = "failed"
                journal.error = "collection correction target is no longer compatible"
                session.flush()
                return "failed", "collection correction target is no longer compatible"
            before_blob["target_group_id"] = target.id
            before_blob["target_is_pinned"] = target.is_pinned
            track.work_unit_id = target.id
            target.is_pinned = True
            dirty_groups.add(target.id)
        else:
            journal.state = "failed"
            journal.error = "unsupported grouping correction action"
            session.flush()
            return "failed", "unsupported grouping correction action"
        session.flush()
        # store final journal before recount
        journal.before_blob = dict(before_blob)
        _recount_groups(session, dirty_groups)
        journal.state = "done"
        session.flush()
        attempt = attempts[operation.id]
        attempt.state = "applied"
        attempt.error = None
        return "applied", None
    except Exception as exc:  # pragma: no cover - defensive
        journal.state = "failed"
        journal.error = str(exc)
        session.flush()
        return "failed", str(exc)


def _bundle_preflight(
    session: Session,
    run: ApplyRun,
    bundle: ReviewBundle,
    files: list[dict[str, object]],
    attempts: dict[int, OperationAttempt],
    library_root: Path | None,
    blob_store: BlobStore | None,
) -> dict[int, str]:
    """Validate entire bundle before any mutation. Returns track_id -> error."""
    from muzilla.changes.writer import _source_precondition_error  # local to avoid cycle

    errors: dict[int, str] = {}
    # 1. Pending operations block whole bundle
    revision = session.get(ProposalRevision, run.proposal_revision_id)
    if revision is None:
        for entry in files:
            errors[cast(int, entry["track_id"])] = "review revision not found"
        return errors
    # Load all ops in revision to detect pending unresolved items
    all_ops = list(
        session.scalars(select(Operation).where(Operation.proposal_revision_id == revision.id))
    )
    pending_ops = [op for op in all_ops if op.decision == "pending"]
    if pending_ops:
        # Any pending means unresolved item blocks entire bundle per spec
        msg = "unresolved items require explicit decision"
        for entry in files:
            errors[cast(int, entry["track_id"])] = msg
        return errors

    # 1b. Explicit matching band: unresolved ambiguous/reject needs Skip or selection
    explanation = revision.match_explanation or {}
    outcome = explanation.get("outcome") if isinstance(explanation, dict) else None
    band = explanation.get("band") if isinstance(explanation, dict) else None
    snapshot = revision.candidate_snapshot or {}
    is_skipped = isinstance(snapshot, dict) and snapshot.get("resolution") == "skipped"
    if (
        not is_skipped
        and bundle.state == "needs_attention"
        and (outcome in ("ambiguous", "candidate_rejected", "zero_results") or band == "ambiguous")
    ):
        msg = "unresolved item requires explicit selection or Skip / Leave unchanged; whole bundle blocked"
        if files:
            for entry in files:
                errors[cast(int, entry["track_id"])] = msg
            return errors
        errors[0] = msg
        return errors

    # 2. Check manifest matches accepted ops
    accepted_ids = sorted(op.id for op in all_ops if op.decision == "accepted")
    manifest_ids = sorted(
        op_id
        for entry in files
        for op_id in cast(list[object], entry.get("operation_ids", []))
        if isinstance(op_id, int)
    )
    if accepted_ids != manifest_ids:
        msg = "review manifest does not match accepted operations; refresh required"
        for entry in files:
            errors[cast(int, entry["track_id"])] = msg
        return errors

    # 3. Per-file validation: collisions, stale sources, track existence, blob existence, dest collisions
    seen_dests: dict[str, int] = {}
    for entry in files:
        track_id = cast(int, entry["track_id"])
        operation_ids = [
            op_id
            for op_id in cast(list[object], entry.get("operation_ids", []))
            if isinstance(op_id, int)
        ]
        # track existence
        track = session.get(Track, track_id)
        if track is None:
            errors[track_id] = f"track {track_id} not found"
            continue
        # blob existence for embed_art
        for op_id in operation_ids:
            op = session.get(Operation, op_id)
            if op is None:
                errors[track_id] = f"operation {op_id} not found"
                break
            # validation collision/errors
            raw_errors = op.validation.get("errors", [])
            validation_errors: list[str] = []
            if isinstance(raw_errors, list):
                validation_errors.extend(str(e) for e in raw_errors if e)
            if op.validation.get("collision"):
                validation_errors.append("unresolved destination collision")
            if validation_errors:
                errors[track_id] = "; ".join(validation_errors)
                break
            # blob check
            if op.kind == OperationKind.EMBED_ART.value:
                if not isinstance(op.proposed_value, dict) or not isinstance(
                    op.proposed_value.get("blob_id"), int
                ):
                    errors[track_id] = "embed_art requires blob_id"
                    break
                blob_id = op.proposed_value["blob_id"]
                if blob_store is not None:
                    if blob_store.get_by_id(session, blob_id) is None:
                        errors[track_id] = f"art blob {blob_id} not found"
                        break
                else:
                    # blob_store not configured but operation requires it
                    errors[track_id] = "embed_art requires blob store"
                    break
            # move dest duplicate detection
            if op.kind == OperationKind.MOVE_FILE.value:
                dest = op.proposed_value
                if not isinstance(dest, str):
                    errors[track_id] = "move_file requires string destination"
                    break
                # normalize dest for duplicate detection — conservative NFC+casefold (no FS I/O)
                import unicodedata

                norm = unicodedata.normalize("NFC", dest.strip()).casefold()
                if norm in seen_dests:
                    errors[track_id] = (
                        f"duplicate destination in bundle: {dest!r} also used by track {seen_dests[norm]}"
                    )
                    errors[seen_dests[norm]] = (
                        f"duplicate destination in bundle: {dest!r} also used by track {track_id}"
                    )
                    break
                seen_dests[norm] = track_id
                # check dest already exists on filesystem (no clobber) - preflight
                if library_root is not None:
                    resolved_root = library_root.resolve()
                    candidate_dest = (
                        Path(dest) if Path(dest).is_absolute() else (resolved_root / dest)
                    )
                    # symlink parent check
                    for parent in candidate_dest.parents:
                        if parent == resolved_root:
                            break
                        if parent.exists() and parent.is_symlink():
                            errors[track_id] = f"refusing to follow symlink: {parent}"
                            break
                    else:
                        # check containment
                        try:
                            resolved_dest = candidate_dest.resolve()
                        except Exception:
                            resolved_dest = candidate_dest
                        if (
                            resolved_root != resolved_dest
                            and resolved_root not in resolved_dest.parents
                        ):
                            errors[track_id] = f"refusing to write outside library root: {dest}"
                            break
                    if track_id in errors:
                        break
                    # existing file check (no clobber) - if dest exists and not same file as source
                    try:
                        if candidate_dest.exists():
                            try:
                                if not Path(track.path).samefile(candidate_dest):
                                    errors[track_id] = (
                                        f"destination already exists: {candidate_dest}"
                                    )
                                    break
                            except OSError:
                                errors[track_id] = f"destination already exists: {candidate_dest}"
                                break
                    except OSError:
                        pass

        if track_id in errors:
            continue
        # source precondition check
        try:
            precond = _source_precondition(entry.get("source"))
        except BundleApplyError as exc:
            errors[track_id] = str(exc)
            continue
        err = _source_precondition_error(track, precond, library_root=library_root)
        if err is not None:
            errors[track_id] = err
            continue

    # 4. Concurrent conflict detection: any other ApplyRun/UndoRun or pending ReviewBundle targeting same track
    if not errors:
        # collect track ids in this bundle
        this_track_ids = {cast(int, e["track_id"]) for e in files}
        # query other ApplyRuns
        other_runs = list(
            session.scalars(
                select(ApplyRun).where(
                    ApplyRun.id != run.id, ApplyRun.state.in_(["pending", "applying"])
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
                other_ids = {
                    cast(int, f.get("track_id"))
                    for f in other_files
                    if isinstance(f, dict) and isinstance(f.get("track_id"), int)
                }
            except Exception:
                continue
            overlap = this_track_ids & other_ids
            if overlap:
                msg = f"concurrent review targets same file(s): {sorted(overlap)}"
                for tid in overlap:
                    errors[tid] = msg
                break
        # pending ReviewBundles / UndoRuns sharing same source track
        if not errors:
            from muzilla.db.models import ReviewUndoRun

            # UndoRuns pending/undoing overlapping same file
            try:
                other_undos = list(
                    session.scalars(
                        select(ReviewUndoRun).where(ReviewUndoRun.state.in_(["pending", "undoing"]))
                    )
                )
                for other_u in other_undos:
                    # UndoRun manifest files or via source ApplyRun
                    u_files: list[dict[str, object]] = []
                    if isinstance(other_u.manifest, dict):
                        raw = other_u.manifest.get("files", [])
                        if isinstance(raw, list):
                            u_files = [f for f in raw if isinstance(f, dict)]
                    if not u_files:
                        # fallback to source ApplyRun tracks
                        src_run = session.get(ApplyRun, other_u.source_apply_run_id)
                        if src_run is not None and isinstance(src_run.manifest, dict):
                            raw2 = src_run.manifest.get("files", [])
                            if isinstance(raw2, list):
                                u_files = [f for f in raw2 if isinstance(f, dict)]
                    u_ids = {
                        cast(int, f.get("track_id"))
                        for f in u_files
                        if isinstance(f.get("track_id"), int)
                    }
                    overlap = this_track_ids & u_ids
                    if overlap:
                        msg = f"concurrent review targets same file(s): {sorted(overlap)}"
                        for tid in overlap:
                            errors[tid] = msg
                        break
                # Pending ReviewBundles sharing source_items (same track_id) - AIR gap check
                if not errors:
                    # ponytail: scan active bundles sharing same source track via current revision snapshot
                    # minimal: check ReviewBundle in preparing/ready/needs_attention overlapping track_id
                    active_states = ["preparing", "ready", "needs_attention"]
                    # avoid loading all snapshots for large DB - only check bundles that are active and not current
                    for other_b in session.scalars(
                        select(ReviewBundle).where(
                            ReviewBundle.id != bundle.id,
                            ReviewBundle.state.in_(active_states),
                        )
                    ):
                        # get current revision payload items
                        cur_rev = session.scalar(
                            select(ProposalRevision).where(
                                ProposalRevision.review_bundle_id == other_b.id,
                                ProposalRevision.is_current.is_(True),
                            )
                        )
                        if cur_rev is None or not isinstance(cur_rev.source_snapshot, dict):
                            continue
                        # source_snapshot may be dict with items or payload dict
                        snap = cur_rev.source_snapshot
                        # handle both dict and object with payload
                        payload = snap.get("payload", snap) if isinstance(snap, dict) else snap
                        if not isinstance(payload, dict):
                            continue
                        raw_items = payload.get("items", [])
                        if not isinstance(raw_items, list):
                            continue
                        o_ids = {
                            cast(int, it.get("source_id"))
                            for it in raw_items
                            if isinstance(it, dict) and isinstance(it.get("source_id"), int)
                        }
                        # also consider path based fallback
                        if not o_ids:
                            o_ids = {
                                cast(int, it.get("track_id"))
                                for it in raw_items
                                if isinstance(it, dict) and isinstance(it.get("track_id"), int)
                            }
                        overlap = this_track_ids & o_ids
                        if overlap:
                            msg = f"concurrent review targets same file(s): {sorted(overlap)}"
                            for tid in overlap:
                                errors[tid] = msg
                            break
            except Exception:
                pass

    # 5. Library root config invalid
    if library_root is not None and not library_root.exists():
        msg = f"library root does not exist: {library_root}"
        for entry in files:
            errors.setdefault(cast(int, entry["track_id"]), msg)

    return errors


def _rollback_applied_files(
    session: Session,
    run: ApplyRun,
    applied_track_ids: list[int],
    library_root: Path | None,
    blob_store: BlobStore | None,
) -> tuple[bool, str | None]:
    """Rollback applied files in reverse order via journal. Returns (ok, error)."""
    from muzilla.changes.writer import (
        _fsync_directory,
        _move_no_clobber,
        _update_track_file_facts,
        restore_from_before_blob,
    )
    from muzilla.domain.metadata import tag_hash as compute_tag_hash
    from muzilla.tags.reader import read_track

    # Reverse to respect move -> tag ordering (moves were last, so rollback moves first)
    for track_id in reversed(applied_track_ids):
        track = session.get(Track, track_id)
        if track is None:
            return False, f"track {track_id} not found during rollback"
        journals = list(
            session.scalars(
                select(ReviewFileJournal)
                .where(
                    ReviewFileJournal.apply_run_id == run.id, ReviewFileJournal.track_id == track_id
                )
                .order_by(ReviewFileJournal.id.desc())
            )
        )
        # journals are in desc order already, but ensure moves before tags: we already reversed track order,
        # and within track, move journals should be after tags (so desc puts move first) - acceptable
        for journal in journals:
            if journal.state != "done":
                continue
            try:
                if journal.phase == "tags":
                    # path for tags rollback is current track path (may have been moved)
                    # Use journal.path as original path before apply, but after moves track.path is after_path
                    # Determine current path to restore: if track was moved, its current path is after_path, but journal.path is old path
                    # We need to restore tags at current location (track.path)
                    cur_path = Path(track.path)
                    if not cur_path.exists():
                        # try journal.path
                        cur_path = Path(journal.path)
                    if not cur_path.exists():
                        return False, f"file missing for tags rollback: {cur_path}"
                    restore_from_before_blob(
                        session, cur_path, journal.before_blob, blob_store=blob_store
                    )
                    # update track facts and blob refs
                    # before_blob contains original field values and art_blob_id
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
                        track.art_blob_id = cast(int | None, val)
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
                                    bool(lyrics.get("synced"))
                                    if isinstance(lyrics, dict)
                                    else False
                                )
                            except Exception:
                                track.lyrics_synced = False
                    after_meta = read_track(cur_path)
                    after_hash = compute_tag_hash(after_meta)
                    _update_track_file_facts(track, cur_path, tag_hash=after_hash)
                    journal.state = "rolled_back"
                    session.flush()
                elif journal.phase == "move":
                    before_path = Path(journal.before_path) if journal.before_path else None
                    after_path = Path(journal.after_path) if journal.after_path else None
                    if before_path is None or after_path is None:
                        return False, "move journal missing before/after path"
                    # after_path is where file currently is (track.path)
                    cur = Path(track.path)
                    # Verify cur is after_path (or resolve)
                    if cur != after_path and cur == before_path:
                        journal.state = "rolled_back"
                        session.flush()
                        continue
                    if not after_path.exists():
                        # Already rolled back or file missing
                        if before_path.exists():
                            journal.state = "rolled_back"
                            session.flush()
                            continue
                        return False, f"file missing for move rollback: {after_path}"
                    if before_path.exists():
                        # Check if same file already
                        try:
                            if before_path.samefile(after_path):
                                journal.state = "rolled_back"
                                session.flush()
                                continue
                        except OSError:
                            pass
                        return False, f"rollback destination already exists: {before_path}"
                    before_path.parent.mkdir(parents=True, exist_ok=True)
                    _move_no_clobber(after_path, before_path, same_file=False)
                    _fsync_directory(before_path.parent)
                    if after_path.parent != before_path.parent:
                        _fsync_directory(after_path.parent)
                    track.path = str(before_path)
                    track.filename = before_path.name
                    after_meta = read_track(before_path)
                    _update_track_file_facts(
                        track, before_path, tag_hash=compute_tag_hash(after_meta)
                    )
                    journal.state = "rolled_back"
                    session.flush()
                elif journal.phase == "grouping":
                    before = journal.before_blob or {}
                    prev_group_id = before.get("group_id")
                    source_group_id = before.get("source_group_id")
                    source_is_pinned = before.get("source_is_pinned")
                    target_group_id = before.get("target_group_id")
                    target_is_pinned = before.get("target_is_pinned")
                    if not isinstance(prev_group_id, int) or not isinstance(source_group_id, int):
                        return False, "grouping journal missing before_group_id"
                    # revert track group
                    track.work_unit_id = prev_group_id
                    # revert pinned flags
                    src = session.get(WorkUnit, source_group_id)
                    if src is not None and isinstance(source_is_pinned, bool):
                        src.is_pinned = source_is_pinned
                    if isinstance(target_group_id, int) and isinstance(target_is_pinned, bool):
                        tgt = session.get(WorkUnit, target_group_id)
                        if tgt is not None:
                            tgt.is_pinned = target_is_pinned
                    # recount affected groups
                    affected = {source_group_id}
                    if isinstance(target_group_id, int):
                        affected.add(target_group_id)
                    affected.add(prev_group_id)
                    _recount_groups(session, affected)
                    journal.state = "rolled_back"
                    session.flush()
                else:
                    continue
            except Exception as exc:
                journal.error = f"rollback failed: {exc}"
                session.commit()
                return False, str(exc)
        session.commit()
    return True, None


def recover_apply_runs(
    session: Session, *, library_root: Path | None = None, blob_store: BlobStore | None = None
) -> int:
    """Startup reconciliation for interrupted ApplyRuns.

    Scans ApplyRuns and ReviewBundles left in applying. If journal shows
    no durable mutations, restores failed state. If subset committed,
    rolls back. If evidence missing, marks recovery_required.
    Returns number of runs recovered.
    """
    recovered = 0
    applying_runs = list(session.scalars(select(ApplyRun).where(ApplyRun.state == "applying")))
    for run in applying_runs:
        bundle = session.get(ReviewBundle, run.review_bundle_id)
        if bundle is None:
            continue
        files = []
        try:
            files = _manifest_files(run)
        except BundleApplyError:
            files = []
        # Check journals
        journals = list(
            session.scalars(
                select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == run.id)
            )
        )
        has_done = any(j.state == "done" for j in journals)
        has_writing = any(j.state == "writing" for j in journals)
        # If no durable done, we can safely mark as failed restored
        if not has_done and not has_writing:
            # No mutations durable - restore to failed without recovery
            for entry in files:
                entry["state"] = "failed"
                entry["error"] = entry.get("error") or "recovered: no durable mutations"
            run.state = "failed"
            run.result = {
                "state": "failed",
                "atomicity": "review_bundle",
                "files": [
                    {
                        "track_id": e.get("track_id"),
                        "state": e.get("state"),
                        "applied_operation_ids": [],
                        "error": e.get("error"),
                    }
                    for e in files
                ],
                "recovery_required": False,
            }
            run.error = "recovered without mutations"
            if bundle:
                bundle.state = "failed"
                bundle.error = run.error
                _sync_inbox_state(session, bundle)
            _refresh_manifest(run, files)
            recovered += 1
            continue
        # If has done, attempt rollback; any writing is ambiguous -> recovery_required
        applied_ids: list[int] = []
        for j in journals:
            if j.state == "done" and j.track_id not in applied_ids:
                applied_ids.append(j.track_id)
        if has_writing:
            # ambiguous: writing journal may have durably mutated file
            if applied_ids:
                ok, err = _rollback_applied_files(
                    session, run, applied_ids, library_root, blob_store
                )
                if ok:
                    for entry in files:
                        tid = entry.get("track_id")
                        if tid in applied_ids:
                            entry["state"] = "rolled_back"
                            entry["error"] = (
                                "recovery_required: interrupted during mutation (rolled back)"
                            )
                else:
                    for entry in files:
                        entry["state"] = "failed"
                        entry["error"] = f"recovery_required: rollback failed: {err}"
                    run.state = "failed"
                    run.result = {
                        "state": "failed",
                        "atomicity": "review_bundle",
                        "files": [
                            {
                                "track_id": e.get("track_id"),
                                "state": e.get("state"),
                                "applied_operation_ids": [],
                                "error": e.get("error"),
                            }
                            for e in files
                        ],
                        "recovery_required": True,
                    }
                    run.error = f"recovery_required: {err}"
                    if bundle:
                        bundle.state = "failed"
                        bundle.error = run.error
                        _sync_inbox_state(session, bundle)
                    _refresh_manifest(run, files)
                    recovered += 1
                    continue
            for entry in files:
                if entry.get("state") != "rolled_back":
                    entry["state"] = "failed"
                    entry["error"] = "recovery_required: interrupted during mutation"
            run.state = "failed"
            run.result = {
                "state": "failed",
                "atomicity": "review_bundle",
                "files": [
                    {
                        "track_id": e.get("track_id"),
                        "state": e.get("state"),
                        "applied_operation_ids": [],
                        "error": e.get("error"),
                    }
                    for e in files
                ],
                "recovery_required": True,
            }
            run.error = "recovery_required: interrupted mutation"
            if bundle:
                bundle.state = "failed"
                bundle.error = run.error
                _sync_inbox_state(session, bundle)
            _refresh_manifest(run, files)
            recovered += 1
        elif applied_ids:
            ok, err = _rollback_applied_files(session, run, applied_ids, library_root, blob_store)
            if ok:
                for entry in files:
                    tid = entry.get("track_id")
                    if tid in applied_ids:
                        entry["state"] = "rolled_back"
                        entry["error"] = "rolled back after crash recovery"
                run.state = "failed"
                run.result = {
                    "state": "failed",
                    "atomicity": "review_bundle",
                    "files": [
                        {
                            "track_id": e.get("track_id"),
                            "state": e.get("state"),
                            "applied_operation_ids": [],
                            "error": e.get("error"),
                        }
                        for e in files
                    ],
                    "recovery_required": False,
                }
                run.error = "recovered via rollback after crash"
                if bundle:
                    bundle.state = "failed"
                    bundle.error = run.error
                    _sync_inbox_state(session, bundle)
                _refresh_manifest(run, files)
                recovered += 1
            else:
                for entry in files:
                    entry["state"] = "failed"
                    entry["error"] = f"recovery_required: rollback failed: {err}"
                run.state = "failed"
                run.result = {
                    "state": "failed",
                    "atomicity": "review_bundle",
                    "files": [
                        {
                            "track_id": e.get("track_id"),
                            "state": e.get("state"),
                            "applied_operation_ids": [],
                            "error": e.get("error"),
                        }
                        for e in files
                    ],
                    "recovery_required": True,
                }
                run.error = f"recovery_required: {err}"
                if bundle:
                    bundle.state = "failed"
                    bundle.error = run.error
                    _sync_inbox_state(session, bundle)
                _refresh_manifest(run, files)
                recovered += 1
        else:
            # no done, no writing already handled; this is has_writing false but no applied_ids (should not happen)
            for entry in files:
                entry["state"] = "failed"
                entry["error"] = "recovery_required: interrupted during mutation"
            run.state = "failed"
            run.result = {
                "state": "failed",
                "atomicity": "review_bundle",
                "files": [
                    {
                        "track_id": e.get("track_id"),
                        "state": e.get("state"),
                        "applied_operation_ids": [],
                        "error": e.get("error"),
                    }
                    for e in files
                ],
                "recovery_required": True,
            }
            run.error = "recovery_required: interrupted mutation"
            if bundle:
                bundle.state = "failed"
                bundle.error = run.error
                _sync_inbox_state(session, bundle)
            _refresh_manifest(run, files)
            recovered += 1
    session.commit()
    # Also handle bundles stuck in applying without run in applying (e.g., run already failed but bundle still applying)
    stuck_bundles = list(
        session.scalars(select(ReviewBundle).where(ReviewBundle.state == "applying"))
    )
    for bundle in stuck_bundles:
        # Find latest run for bundle
        latest = session.scalar(
            select(ApplyRun)
            .where(ApplyRun.review_bundle_id == bundle.id)
            .order_by(ApplyRun.id.desc())
            .limit(1)
        )
        if latest is None or latest.state != "applying":
            bundle.state = "failed"
            bundle.error = "recovered: bundle stuck in applying without active run"
            _sync_inbox_state(session, bundle)
            recovered += 1
    session.commit()
    return recovered


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
        return BundleApplyResult(run.id, bundle.id, "applied", results, recovery_required=False)
    # Also treat already failed as idempotent if previously finalized with same manifest
    if run.state == "failed" and run.result is not None:
        # If caller retries same idempotency, service layer will enqueue new job, but handler may re-enter same run
        # Allow re-entry only if caller explicitly retries via new job; for idempotent apply, return previous result
        # Check if all files already terminal
        results = tuple(_file_result(entry, attempts) for entry in files)
        recovery_required = (
            bool(run.result.get("recovery_required")) if isinstance(run.result, dict) else False
        )
        return BundleApplyResult(
            run.id, bundle.id, run.state, results, recovery_required=recovery_required
        )
    run.state = "applying"
    if bundle.state != "applying":
        bundle.state = "applying"
    _sync_inbox_state(session, bundle)
    session.commit()

    # Bundle-level preflight before any mutation
    preflight_errors = _bundle_preflight(
        session, run, bundle, files, attempts, library_root, blob_store
    )
    if preflight_errors:
        # Validation errors write nothing - no journals created, no FS mutation
        first_error = next(iter(preflight_errors.values()))
        for entry in files:
            tid = cast(int, entry["track_id"])
            err = preflight_errors.get(tid, first_error)
            entry["state"] = "failed"
            entry["error"] = err
            # mark operation attempts for this file as failed
            for op_id in [
                oid
                for oid in cast(list[object], entry.get("operation_ids", []))
                if isinstance(oid, int)
            ]:
                if op_id in attempts:
                    attempts[op_id].state = "failed"
                    attempts[op_id].error = err
        # also mark any other attempts not tied to files? but all are tied
        run.state = "failed"
        run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [
                {
                    "track_id": e.get("track_id"),
                    "state": e.get("state"),
                    "applied_operation_ids": [],
                    "error": e.get("error"),
                }
                for e in files
            ],
            "recovery_required": False,
        }
        run.error = first_error
        bundle.state = "failed"
        bundle.error = run.error
        _sync_inbox_state(session, bundle)
        _refresh_manifest(run, files)
        session.commit()
        results = tuple(_file_result(entry, attempts) for entry in files)
        errors = {r.track_id: r.error for r in results if r.error is not None}
        return BundleApplyResult(
            run.id, bundle.id, "failed", results, errors, cancelled=False, recovery_required=False
        )

    cancelled = False
    applied_track_ids: list[int] = []
    failure_track_id: int | None = None
    failure_error: str | None = None

    for entry in files:
        if should_cancel is not None and should_cancel():
            cancelled = True
            failure_error = "cancelled before file apply"
            # mark remaining pending entries as failed due to cancellation
            for pending_entry in files:
                if (
                    pending_entry.get("state") not in {"applied", "rolled_back"}
                    and pending_entry.get("state") == "pending"
                ):
                    pending_entry["state"] = "failed"
                    pending_entry["error"] = "cancelled before file apply"
            # mark operation attempts for remaining as failed
            for pending_entry in files:
                if (
                    pending_entry.get("state") == "failed"
                    and pending_entry.get("error") == "cancelled before file apply"
                ):
                    for op_id in [
                        oid
                        for oid in cast(list[object], pending_entry.get("operation_ids", []))
                        if isinstance(oid, int)
                    ]:
                        if op_id in attempts and attempts[op_id].state == "pending":
                            attempts[op_id].state = "failed"
                            attempts[op_id].error = "cancelled before file apply"
            _refresh_manifest(run, files)
            session.commit()
            break
        operation_ids_for_entry = [
            op_id
            for op_id in cast(list[object], entry.get("operation_ids", []))
            if isinstance(op_id, int)
        ]
        if operation_ids_for_entry and all(
            attempts[op_id].state == "applied" for op_id in operation_ids_for_entry
        ):
            entry["state"] = "applied"
            entry["error"] = None
            if cast(int, entry["track_id"]) not in applied_track_ids:
                applied_track_ids.append(cast(int, entry["track_id"]))
        if entry.get("state") in {"applied", "skipped"}:
            continue
        track_id = cast(int, entry["track_id"])
        operation_ids = [
            op_id
            for op_id in cast(list[object], entry.get("operation_ids", []))
            if isinstance(op_id, int)
        ]
        retry_ids = [
            op_id for op_id in operation_ids if attempts[op_id].state in {"pending", "failed"}
        ]
        if not retry_ids:
            entry["state"] = (
                "applied"
                if all(attempts[op_id].state == "applied" for op_id in operation_ids)
                else "skipped"
            )
            continue
        track = session.get(Track, track_id)
        if track is None:
            missing_error = f"track {track_id} not found"
            entry["state"] = "failed"
            entry["error"] = missing_error
            failure_track_id = track_id
            failure_error = missing_error
            for op_id in retry_ids:
                attempts[op_id].state = "failed"
                attempts[op_id].error = missing_error
            _refresh_manifest(run, files)
            session.commit()
            break
        operations = _operations(session, retry_ids)
        # grouping corrections cannot be mixed with file operations - this is also bundle-level but check per file
        grouping_ops = [
            op for op in operations if op.kind == OperationKind.GROUPING_CORRECTION.value
        ]
        if grouping_ops:
            if len(grouping_ops) != len(operations):
                mixed_error = "grouping corrections cannot be mixed with file operations"
                entry["state"] = "failed"
                entry["error"] = mixed_error
                failure_track_id = track_id
                failure_error = mixed_error
                for operation in operations:
                    attempts[operation.id].state = "failed"
                    attempts[operation.id].error = mixed_error
                _refresh_manifest(run, files)
                session.commit()
                break
            file_state, grouping_error = _apply_grouping_correction(
                session, track, grouping_ops, attempts, apply_run_id=run.id
            )
            entry["state"] = file_state
            entry["error"] = grouping_error
            if grouping_error is not None:
                for op in grouping_ops:
                    attempts[op.id].state = "failed"
                    attempts[op.id].error = grouping_error
                failure_track_id = track_id
                failure_error = grouping_error
                _refresh_manifest(run, files)
                session.commit()
                break
            # success
            applied_track_ids.append(track_id)
            _refresh_manifest(run, files)
            session.commit()
            continue
        # Determine precondition
        already_applied = any(attempts[op_id].state == "applied" for op_id in operation_ids)
        try:
            if already_applied:
                precondition = _current_precondition(track)
            else:
                precondition = _source_precondition(entry.get("source"))
        except BundleApplyError as exc:
            entry["state"] = "failed"
            entry["error"] = str(exc)
            failure_track_id = track_id
            failure_error = str(exc)
            for op_id in retry_ids:
                attempts[op_id].state = "conflicted"
                attempts[op_id].error = str(exc)
            _refresh_manifest(run, files)
            session.commit()
            break
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
                if (
                    op.kind == OperationKind.SET_TAG.value
                    or op.kind == OperationKind.SET_REPLAY_GAIN.value
                ):
                    field_values[op.field] = op.proposed_value
                elif op.kind == OperationKind.EMBED_ART.value:
                    if isinstance(op.proposed_value, dict) and isinstance(
                        op.proposed_value.get("blob_id"), int
                    ):
                        art_blob_id = op.proposed_value["blob_id"]
                elif op.kind == OperationKind.REMOVE_ART.value:
                    remove_art = True
                elif op.kind == OperationKind.WRITE_LYRICS.value:
                    if isinstance(op.proposed_value, dict):
                        lyrics_payload = op.proposed_value
                elif op.kind == OperationKind.GROUPING_CORRECTION.value:
                    pass
            tag_ok, tag_err = write_tag_fields(
                session,
                apply_run_id=run.id,
                track=track,
                field_values=field_values,
                art_blob_id=art_blob_id,
                remove_art=remove_art,
                lyrics_payload=lyrics_payload,
                lyrics_remove=lyrics_remove,
                blob_store=blob_store,
                backup_store=backup_store,
                source_precondition=precondition,
                library_root=library_root,
            )
            if not tag_ok:
                assert tag_err is not None
                file_state = "failed"
                file_error = tag_err
                failure_track_id = track_id
                failure_error = tag_err
                for op in tag_ops:
                    if attempts[op.id].state != "applied":
                        attempts[op.id].state = (
                            "failed" if "snapshot" not in tag_err else "conflicted"
                        )
                        attempts[op.id].error = tag_err
            else:
                for op in tag_ops:
                    attempts[op.id].state = "applied"
                    attempts[op.id].error = None
        if file_state == "applied" and move_ops:
            move_op = move_ops[0]
            dest = move_op.proposed_value
            if not isinstance(dest, str):
                file_state = "failed"
                file_error = "move_file requires string destination"
                failure_track_id = track_id
                failure_error = file_error
                for op in move_ops:
                    attempts[op.id].state = "failed"
                    attempts[op.id].error = file_error
            else:
                ok_move, err_move = write_move(
                    session,
                    apply_run_id=run.id,
                    track=track,
                    destination=dest,
                    library_root=library_root,
                    create_directories=create_directories,
                )
                if not ok_move:
                    assert err_move is not None
                    file_state = "failed"
                    file_error = err_move
                    failure_track_id = track_id
                    failure_error = err_move
                    for op in move_ops:
                        attempts[op.id].state = "failed"
                        attempts[op.id].error = err_move
                else:
                    for op in move_ops:
                        attempts[op.id].state = "applied"
                        attempts[op.id].error = None
        entry["state"] = file_state
        entry["error"] = file_error
        _refresh_manifest(run, files)
        session.commit()
        if file_state == "applied":
            applied_track_ids.append(track_id)
        else:
            # runtime failure - need to rollback prior successes atomically
            break

    # Determine final outcome - atomic: if any failure or cancelled, rollback prior successes
    # Includes current track's tag journal if tag succeeded but move failed.
    recovery_required = False
    rollback_error: str | None = None
    if cancelled or failure_error is not None:
        rollback_ids = list(applied_track_ids)
        if failure_track_id is not None and failure_track_id not in rollback_ids:
            has_done = session.scalar(
                select(ReviewFileJournal.id)
                .where(
                    ReviewFileJournal.apply_run_id == run.id,
                    ReviewFileJournal.track_id == failure_track_id,
                    ReviewFileJournal.state == "done",
                )
                .limit(1)
            )
            if has_done is not None:
                rollback_ids.append(failure_track_id)
        # Need rollback of previously applied files (if any)
        if rollback_ids:
            rollback_ok, rollback_err = _rollback_applied_files(
                session, run, rollback_ids, library_root, blob_store
            )
            if rollback_ok:
                # mark rolled_back entries
                for entry in files:
                    tid = cast(int, entry["track_id"])
                    if tid in rollback_ids:
                        # preserve failing track as failed, others as rolled_back
                        if tid == failure_track_id:
                            continue
                        entry["state"] = "rolled_back"
                        entry["error"] = "rolled back after bundle failure"
                        # mark their attempts as rolled_back (use failed with message to keep DB constraint)
                        for op_id in [
                            oid
                            for oid in cast(list[object], entry.get("operation_ids", []))
                            if isinstance(oid, int)
                        ]:
                            if op_id in attempts and attempts[op_id].state == "applied":
                                attempts[op_id].state = "failed"
                                attempts[op_id].error = "rolled back after bundle failure"
                # also ensure the failing file itself stays failed
                if failure_track_id is not None:
                    for entry in files:
                        if cast(int, entry["track_id"]) == failure_track_id:
                            entry["state"] = "failed"
                            entry["error"] = failure_error
                            # also mark any applied tag ops on failing track as rolled back if journal was rolled back
                            # their attempts were already set to failed for tag success, now need to reflect rolled back state via journal, but DB constraint keeps failed
                            for op_id in [
                                oid
                                for oid in cast(list[object], entry.get("operation_ids", []))
                                if isinstance(oid, int)
                            ]:
                                if op_id in attempts and attempts[op_id].state == "applied":
                                    attempts[op_id].state = "failed"
                                    attempts[op_id].error = "rolled back after bundle failure"
                # remaining pending files that were not yet processed should be marked failed due to bundle failure
                for entry in files:
                    if entry.get("state") == "pending":
                        entry["state"] = "failed"
                        entry["error"] = failure_error or "cancelled before file apply"
                        for op_id in [
                            oid
                            for oid in cast(list[object], entry.get("operation_ids", []))
                            if isinstance(oid, int)
                        ]:
                            if op_id in attempts and attempts[op_id].state == "pending":
                                attempts[op_id].state = "failed"
                                attempts[op_id].error = (
                                    failure_error or "cancelled before file apply"
                                )
            else:
                recovery_required = True
                rollback_error = rollback_err
                # keep applied as is but mark recovery_required in result
                for entry in files:
                    tid = cast(int, entry["track_id"])
                    if tid in rollback_ids and entry.get("state") == "applied":
                        entry["error"] = f"recovery_required: rollback failed: {rollback_err}"
        else:
            # No prior committed files, just ensure remaining pending marked
            for entry in files:
                if entry.get("state") == "pending":
                    entry["state"] = "failed"
                    entry["error"] = failure_error or "cancelled before file apply"
                    for op_id in [
                        oid
                        for oid in cast(list[object], entry.get("operation_ids", []))
                        if isinstance(oid, int)
                    ]:
                        if op_id in attempts and attempts[op_id].state == "pending":
                            attempts[op_id].state = "failed"
                            attempts[op_id].error = failure_error or "cancelled before file apply"
            # also ensure failing track's tag done is handled even if no prior ids: check again for rollback_ids empty case
            if failure_track_id is not None:
                has_done2 = session.scalar(
                    select(ReviewFileJournal.id)
                    .where(
                        ReviewFileJournal.apply_run_id == run.id,
                        ReviewFileJournal.track_id == failure_track_id,
                        ReviewFileJournal.state == "done",
                    )
                    .limit(1)
                )
                if has_done2 is not None:
                    # need to attempt rollback for this single track even though applied_track_ids was empty
                    ok2, err2 = _rollback_applied_files(
                        session, run, [failure_track_id], library_root, blob_store
                    )
                    if not ok2:
                        recovery_required = True
                        rollback_error = err2

    # Final state determination - never partially_applied as success
    # Applied only if no failure, no cancellation, and all attempts applied
    applied_count = sum(1 for a in attempts.values() if a.state == "applied")
    total = len(attempts)
    failed_or_cancelled = cancelled or failure_error is not None or recovery_required
    if not failed_or_cancelled and total > 0 and applied_count == total:
        final_state = "applied"
    else:
        final_state = "failed"

    results = tuple(_file_result(entry, attempts) for entry in files)
    errors = {r.track_id: r.error for r in results if r.error is not None}
    # Build result payload
    result_files = []
    for r in results:
        result_files.append(
            {
                "track_id": r.track_id,
                "state": r.state,
                "applied_operation_ids": list(r.applied_operation_ids),
                "error": r.error,
            }
        )
    run.state = final_state
    run.result = {
        "state": final_state,
        "atomicity": "review_bundle",
        "files": result_files,
        "recovery_required": recovery_required,
    }
    if recovery_required:
        run.error = f"recovery_required: {rollback_error or failure_error or 'rollback failed'}"
    elif cancelled:
        run.error = failure_error or "cancelled before completion"
    elif failure_error is not None:
        # If rolled back successfully, error should still indicate original failure but with rolled back note
        run.error = failure_error if not applied_track_ids else f"{failure_error} (rolled back)"
        if recovery_required:
            run.error = f"recovery_required: {run.error}"
    else:
        run.error = (
            None
            if final_state == "applied"
            else "; ".join(f"track {tid}: {err}" for tid, err in errors.items()) or None
        )
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
        recovery_required=recovery_required,
    )


def build_undo_changesets_for_run(session: Session, apply_run_id: int) -> tuple[object, ...]:
    # Native undo not via ChangeSet; return empty tuple for compatibility
    # Real undo is via ReviewUndoRun
    return ()
