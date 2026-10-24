"""Application service for persistent ReviewBundle apply enqueue/retry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update  # pyright: ignore[reportMissingImports]
from sqlalchemy.orm import Session  # pyright: ignore[reportMissingImports]

from muzilla.config.schema import Config
from muzilla.db.models import ApplyRun, Job, ReviewBundle
from muzilla.db.source_snapshots import (
    read_source_snapshot_items,
    read_source_snapshot_track_ids,
)
from muzilla.domain.reviews import PHYSICAL_MUTATION_KINDS
from muzilla.jobs import queue

# Register the worker handler when this service is the API entry point.
from muzilla.jobs.handlers import apply as _apply_handler  # noqa: F401
from muzilla.jobs.sync import run_sync
from muzilla.pipeline.reviews import ReviewInvariantError, start_apply_run
from muzilla.services.db import session_scope


@dataclass(frozen=True, slots=True)
class ReviewApplyEnqueued:
    apply_run_id: int
    job_id: int


def _job_ids(run: ApplyRun) -> list[int]:
    raw = run.manifest.get("job_ids", [])
    if not isinstance(raw, list):
        return []
    return [value for value in raw if isinstance(value, int)]


def _active_or_completed_job(session: Session, run: ApplyRun) -> Job | None:
    ids = _job_ids(run)
    if not ids:
        return None
    jobs = {job.id: job for job in session.scalars(select(Job).where(Job.id.in_(ids)))}
    for job_id in reversed(ids):
        job = jobs.get(job_id)
        if job is not None and job.state in {"pending", "running", "cancelling"}:
            return job
    if run.state == "applied":
        return next((jobs[job_id] for job_id in reversed(ids) if job_id in jobs), None)
    return None


def _preflight_for_apply(
    session: Session,
    bundle: ReviewBundle,
    library_root: Path,
) -> tuple[dict[int, dict[str, object]] | None, int | None]:
    """Synchronous REVIEW-CONFLICTS-001 preflight before 202. Raises 409 with code."""
    from muzilla.changes.bundle_applier import (
        BundleApplyError,
        _journal_execution_source,
        _manifest_files,
        _operation_attempts,
        _operations,
        _rolled_back_source_item,
        _verified_execution_source_item,
    )
    from muzilla.changes.writer import SourcePrecondition, _source_precondition_error
    from muzilla.db.models import ApplyRun, ProposalRevision, ReviewUndoRun, Track
    from muzilla.db.models import ReviewBundle as RB

    cur = session.scalar(
        select(ProposalRevision).where(
            ProposalRevision.review_bundle_id == bundle.id,
            ProposalRevision.is_current.is_(True),
        )
    )
    if cur is None:
        raise ReviewInvariantError("stale_source: review has no current revision")
    try:
        raw_items = read_source_snapshot_items(cur.source_snapshot)
    except ValueError as exc:
        raise ReviewInvariantError("stale_source: source snapshot is incomplete") from exc
    items_by_id = {item.track_id: item for item in raw_items}
    physical_kinds = {kind.value for kind in PHYSICAL_MUTATION_KINDS}
    accepted_physical = []
    track_ids = set(items_by_id)
    for operation in cur.operations:
        if operation.target_type == "track":
            track_ids.add(operation.target_id)
        if operation.decision != "accepted" or operation.kind not in physical_kinds:
            continue
        if operation.target_type != "track":
            raise ReviewInvariantError("stale_source: unsupported physical operation target")
        accepted_physical.append(operation)
    # Runtime checkpoints may replace only the current comparison baseline, never the preview evidence.
    check_items = dict(items_by_id)
    retry_sources: dict[int, dict[str, object]] = {}
    retry_source_run_id: int | None = None
    if bundle.state in {"applying", "partially_applied"}:
        active_run = session.scalar(
            select(ApplyRun)
            .where(
                ApplyRun.review_bundle_id == bundle.id,
                ApplyRun.state.in_(("pending", "applying", "partially_applied")),
            )
            .order_by(ApplyRun.id.desc())
            .limit(1)
        )
        if active_run is None or active_run.proposal_revision_id != cur.id:
            raise ReviewInvariantError("stale_source: active apply checkpoint is unavailable")
        try:
            active_entries = {
                track_id: entry
                for entry in _manifest_files(active_run)
                if isinstance(entry.get("track_id"), int)
                and not isinstance(track_id := entry.get("track_id"), bool)
            }
            attempts = _operation_attempts(active_run)
            for tid in {operation.target_id for operation in accepted_physical}:
                source_item = items_by_id.get(tid)
                entry = active_entries.get(tid)
                if source_item is None or entry is None:
                    raise BundleApplyError("active apply manifest omits a reviewed target")
                execution_item = _verified_execution_source_item(
                    session, active_run, tid, source_item, entry
                )
                raw_op_ids = entry.get("operation_ids", [])
                if not isinstance(raw_op_ids, list):
                    raise BundleApplyError("active apply manifest operations are invalid")
                op_ids = [
                    op_id
                    for op_id in raw_op_ids
                    if isinstance(op_id, int) and not isinstance(op_id, bool)
                ]
                operations = _operations(session, op_ids)
                if any(attempts[op.id].state == "applied" for op in operations):
                    check_items[tid] = _journal_execution_source(
                        session, active_run, tid, execution_item, operations, attempts
                    )
                else:
                    check_items[tid] = execution_item
        except (BundleApplyError, KeyError, TypeError, ValueError) as exc:
            raise ReviewInvariantError(
                f"stale_source: active apply checkpoint is invalid: {exc}"
            ) from exc
    elif bundle.state == "failed":
        previous_run = session.scalar(
            select(ApplyRun)
            .where(ApplyRun.review_bundle_id == bundle.id)
            .order_by(ApplyRun.id.desc())
            .limit(1)
        )
        if previous_run is not None and previous_run.proposal_revision_id == cur.id:
            for tid in {operation.target_id for operation in accepted_physical}:
                source_item = items_by_id.get(tid)
                if source_item is None:
                    continue
                retry_item = _rolled_back_source_item(session, previous_run, tid, source_item)
                if retry_item is not None and retry_item != source_item:
                    retry_sources[tid] = retry_item.to_manifest_item()
                    check_items[tid] = retry_item
            if retry_sources:
                retry_source_run_id = previous_run.id

    # Validate every accepted file target, even when its source snapshot is empty or incomplete.
    stale_files: list[int] = []
    for operation in accepted_physical:
        tid = operation.target_id
        src_entry = check_items.get(tid)
        track = session.get(Track, tid)
        if (
            track is None
            or src_entry is None
            or src_entry.path is None
            or src_entry.size_bytes is None
            or src_entry.mtime_ns is None
            or src_entry.tag_hash is None
            or src_entry.physical_guard is None
        ):
            stale_files.append(tid)
            continue
        precond = SourcePrecondition(
            path=src_entry.path,
            size_bytes=src_entry.size_bytes,
            mtime_ns=src_entry.mtime_ns,
            tag_hash=src_entry.tag_hash,
            physical_guard=src_entry.physical_guard,
        )
        if _source_precondition_error(track, precond, library_root=library_root) is not None:
            stale_files.append(tid)
    if stale_files:
        raise ReviewInvariantError(
            f"stale_source: files {sorted(set(stale_files))} changed after preview; refresh required"
        )
    # 1b. Matching decision: unresolved ambiguous/reject blocks whole bundle until explicit Skip/selection
    # PATH-COLLISION-001: block Apply when destination collision or validation errors exist
    for operation in list(cur.operations):
        validation = getattr(operation, "validation", None)
        if not isinstance(validation, dict):
            continue
        if validation.get("collision"):
            raise ReviewInvariantError("unresolved destination collision; whole bundle blocked")
        errors_raw = validation.get("errors")
        if isinstance(errors_raw, list) and any(str(error).strip() for error in errors_raw):
            raise ReviewInvariantError("; ".join(str(error) for error in errors_raw))

    explanation = cur.match_explanation or {}
    outcome = explanation.get("outcome") if isinstance(explanation, dict) else None
    band = explanation.get("band") if isinstance(explanation, dict) else None
    snapshot = cur.candidate_snapshot or {}
    is_skipped = isinstance(snapshot, dict) and snapshot.get("resolution") == "skipped"
    if (
        not is_skipped
        and bundle.state == "needs_attention"
        and (outcome in ("ambiguous", "candidate_rejected", "zero_results") or band == "ambiguous")
    ):
        raise ReviewInvariantError(
            "unresolved item requires explicit selection or Skip / Leave unchanged; whole bundle blocked"
        )
    # 2) concurrent Apply/Undo and pending ReviewBundle check
    # pending ReviewBundles sharing same source
    for other in session.scalars(
        select(RB).where(
            RB.id != bundle.id, RB.state.in_(["preparing", "ready", "needs_attention"])
        )
    ):
        oc = session.scalar(
            select(ProposalRevision).where(
                ProposalRevision.review_bundle_id == other.id, ProposalRevision.is_current.is_(True)
            )
        )
        if oc is None:
            raise ReviewInvariantError(
                "concurrent_conflict: another active review has no current revision"
            )
        try:
            required_ids = {
                operation.target_id
                for operation in oc.operations
                if operation.target_type == "track"
            }
            if other.scope_type == "track":
                if other.scope_id is None:
                    raise ValueError("active track review has no scope id")
                required_ids.add(other.scope_id)
            elif other.scope_type == "group":
                if other.scope_id is None:
                    raise ValueError("active group review has no scope id")
                required_ids.update(
                    session.scalars(select(Track.id).where(Track.work_unit_id == other.scope_id))
                )
            else:
                raise ValueError("active review has an unsupported scope")
            o_ids = read_source_snapshot_track_ids(oc.source_snapshot, required_ids)
        except ValueError as exc:
            raise ReviewInvariantError(
                f"concurrent_conflict: another active review has incomplete source coverage: {exc}"
            ) from exc
        if track_ids & o_ids:
            raise ReviewInvariantError(
                f"concurrent_conflict: files {sorted(track_ids & o_ids)} already under review"
            )
    # ApplyRun pending/applying
    for other_run in session.scalars(
        select(ApplyRun).where(ApplyRun.state.in_(["pending", "applying"]))
    ):
        if other_run.review_bundle_id == bundle.id:
            continue
        if not isinstance(other_run.manifest, dict):
            continue
        raw = other_run.manifest.get("files", [])
        if not isinstance(raw, list):
            continue
        o_ids2 = {
            f["track_id"] for f in raw if isinstance(f, dict) and isinstance(f.get("track_id"), int)
        }
        if track_ids & o_ids2:
            raise ReviewInvariantError(
                f"concurrent_conflict: files {sorted(track_ids & o_ids2)} already under review"
            )
    for other_u in session.scalars(
        select(ReviewUndoRun).where(ReviewUndoRun.state.in_(["pending", "undoing"]))
    ):
        if not isinstance(other_u.manifest, dict):
            continue
        raw = other_u.manifest.get("files", [])
        if not isinstance(raw, list):
            continue
        o_ids3 = {
            f["track_id"] for f in raw if isinstance(f, dict) and isinstance(f.get("track_id"), int)
        }
        if track_ids & o_ids3:
            raise ReviewInvariantError(
                f"concurrent_conflict: files {sorted(track_ids & o_ids3)} already under review"
            )

    return (retry_sources or None), retry_source_run_id


def enqueue_review_apply(
    session: Session,
    review_bundle_id: int,
    *,
    idempotency_key: str,
    library_root: Path,
    backup: bool | None = None,
) -> ReviewApplyEnqueued:
    bundle = session.get(ReviewBundle, review_bundle_id)
    if bundle is None:
        raise ReviewInvariantError(f"review bundle {review_bundle_id} not found")
    existing_run = session.scalar(
        select(ApplyRun).where(
            ApplyRun.review_bundle_id == review_bundle_id,
            ApplyRun.idempotency_key == idempotency_key,
        )
    )
    if existing_run is not None:
        existing_job = _active_or_completed_job(session, existing_run)
        if existing_job is not None:
            return ReviewApplyEnqueued(existing_run.id, existing_job.id)
    # REVIEW-CONFLICTS-001: synchronous preflight before a new or retried enqueue
    execution_sources, retry_source_run_id = _preflight_for_apply(session, bundle, library_root)

    # ponytail: partially_applied is deprecated - kept only for legacy retry path
    if bundle.state in {"partially_applied", "applying"}:
        run = session.scalar(
            select(ApplyRun)
            .where(ApplyRun.review_bundle_id == review_bundle_id)
            .order_by(ApplyRun.id.desc())
        )
        if run is None:
            raise ReviewInvariantError("review has no apply run to resume")
        if (
            run.result is not None
            and isinstance(run.result, dict)
            and bool(run.result.get("recovery_required"))
        ):
            raise ReviewInvariantError("review requires recovery before retry")
    elif bundle.state == "failed":
        latest = session.scalar(
            select(ApplyRun)
            .where(ApplyRun.review_bundle_id == review_bundle_id)
            .order_by(ApplyRun.id.desc())
        )
        if latest is None:
            raise ReviewInvariantError("review has no apply run to resume")
        if (
            latest.result is not None
            and isinstance(latest.result, dict)
            and bool(latest.result.get("recovery_required"))
        ):
            raise ReviewInvariantError("review requires recovery before retry")
        # reuse only if still pending/applying; failed but recoverable creates a new run
        if latest.state in {"pending", "applying"}:
            run = latest
        else:
            run = start_apply_run(
                session,
                review_bundle_id,
                idempotency_key=idempotency_key,
                execution_sources=execution_sources,
                retry_source_run_id=retry_source_run_id,
            )
    else:
        run = start_apply_run(
            session,
            review_bundle_id,
            idempotency_key=idempotency_key,
            execution_sources=execution_sources,
            retry_source_run_id=retry_source_run_id,
        )

    existing_job = _active_or_completed_job(session, run)
    if existing_job is not None:
        return ReviewApplyEnqueued(run.id, existing_job.id)

    payload: dict[str, object] = {"apply_run_id": run.id}
    if backup is not None:
        payload["backup"] = backup
    job = queue.enqueue(
        session,
        type="apply_review_bundle",
        payload=payload,
        commit=False,
    )
    manifest = dict(run.manifest)
    manifest["job_ids"] = [*_job_ids(run), job.id]
    run.manifest = manifest
    session.commit()
    return ReviewApplyEnqueued(run.id, job.id)


async def enqueue_review_apply_async(
    config: Config,
    review_bundle_id: int,
    *,
    idempotency_key: str,
    backup: bool | None = None,
) -> ReviewApplyEnqueued:
    """Supervise preflight and durable enqueue with a thread-owned session."""

    def invoke() -> ReviewApplyEnqueued:
        with session_scope(config) as session:
            return enqueue_review_apply(
                session,
                review_bundle_id,
                idempotency_key=idempotency_key,
                library_root=config.storage.library_root,
                backup=backup,
            )

    return await run_sync(invoke)


def recover_atomic_bundles(session: Session, config: Config) -> int:
    """Startup recovery for atomic ReviewBundle journals. Delegates to bundle_applier."""
    from muzilla.changes.blobstore import BlobStore
    from muzilla.changes.bundle_applier import recover_apply_runs

    return recover_apply_runs(
        session,
        library_root=config.storage.library_root,
        blob_store=BlobStore(config.storage.blob_dir),
    )


def mark_recovery_required_for_stuck_apply_runs(session: Session) -> int:
    """Fail closed any still-applying ApplyRuns/ReviewBundles after recovery exception."""
    from muzilla.db.models import ReviewInboxEntry

    marked = 0
    for _run in list(session.scalars(select(ApplyRun).where(ApplyRun.state == "applying"))):
        _run.state = "failed"
        _run.error = "recovery_required: startup recovery failed"
        _run.result = {
            "state": "failed",
            "atomicity": "review_bundle",
            "files": [],
            "recovery_required": True,
        }
        _b = session.get(ReviewBundle, _run.review_bundle_id)
        if _b is not None and _b.state == "applying":
            _b.state = "failed"
            _b.error = _run.error
            session.execute(
                update(ReviewInboxEntry)
                .where(ReviewInboxEntry.review_bundle_id == _b.id)
                .values(state=_b.state, issue_kind="review", issue_message=_b.error)
            )
        marked += 1
    for _bundle in list(
        session.scalars(select(ReviewBundle).where(ReviewBundle.state == "applying"))
    ):
        _bundle.state = "failed"
        _bundle.error = "recovery_required: startup recovery failed"
        session.execute(
            update(ReviewInboxEntry)
            .where(ReviewInboxEntry.review_bundle_id == _bundle.id)
            .values(state=_bundle.state, issue_kind="review", issue_message=_bundle.error)
        )
        marked += 1
    return marked
