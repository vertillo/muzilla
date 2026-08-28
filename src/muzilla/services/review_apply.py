"""Application service for persistent ReviewBundle apply enqueue/retry."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update  # pyright: ignore[reportMissingImports]
from sqlalchemy.orm import Session  # pyright: ignore[reportMissingImports]

from muzilla.config.schema import Config
from muzilla.db.models import ApplyRun, Job, ReviewBundle
from muzilla.jobs import queue

# Register the worker handler when this service is the API entry point.
from muzilla.jobs.handlers import apply as _apply_handler  # noqa: F401
from muzilla.pipeline.reviews import ReviewInvariantError, start_apply_run


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
    library_root: object | None = None,
) -> None:
    """Synchronous REVIEW-CONFLICTS-001 preflight before 202. Raises 409 with code."""
    from pathlib import Path

    from muzilla.changes.writer import SourcePrecondition, _source_precondition_error
    from muzilla.db.models import ProposalRevision, ReviewUndoRun, Track
    from muzilla.db.models import ReviewBundle as RB

    cur = session.scalar(
        select(ProposalRevision).where(
            ProposalRevision.review_bundle_id == bundle.id,
            ProposalRevision.is_current.is_(True),
        )
    )
    if cur is None or not isinstance(cur.source_snapshot, dict):
        return
    # Extract track ids from snapshot payload
    payload = (
        cur.source_snapshot.get("payload", cur.source_snapshot)
        if isinstance(cur.source_snapshot, dict)
        else cur.source_snapshot
    )
    if not isinstance(payload, dict):
        return
    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        return
    track_ids = {
        it["source_id"]
        for it in raw_items
        if isinstance(it, dict) and isinstance(it.get("source_id"), int)
    }
    if not track_ids:
        track_ids = {
            it["track_id"]
            for it in raw_items
            if isinstance(it, dict) and isinstance(it.get("track_id"), int)
        }
    if not track_ids:
        return
    # 1) stale check via _source_precondition_error (includes mtime/size now)
    stale_files: list[int] = []
    for tid in track_ids:
        track = session.get(Track, tid)
        if track is None:
            stale_files.append(tid)
            continue
        # find source entry for this tid
        src_entry = next(
            (it for it in raw_items if isinstance(it, dict) and it.get("source_id") == tid), None
        )
        if src_entry is None:
            src_entry = next(
                (it for it in raw_items if isinstance(it, dict) and it.get("track_id") == tid), None
            )
        if not isinstance(src_entry, dict):
            continue
        try:
            precond = SourcePrecondition(
                path=str(src_entry.get("path", "")),
                size_bytes=int(src_entry.get("size_bytes", 0)),
                mtime_ns=int(src_entry.get("mtime_ns", 0)),
                tag_hash=str(src_entry.get("tag_hash", "")),
            )
        except Exception:
            stale_files.append(tid)
            continue
        # library_root from config if available
        lr = None
        try:
            from muzilla.config.loader import load_config

            lr = Path(load_config().storage.library_root)
        except Exception:
            lr = None
        err = _source_precondition_error(track, precond, library_root=lr)
        if err is not None:
            stale_files.append(tid)
    if stale_files:
        raise ReviewInvariantError(
            f"stale_source: files {sorted(stale_files)} changed after preview; refresh required"
        )
    # 2) concurrent Apply/Undo and pending ReviewBundle check
    from muzilla.db.models import ApplyRun

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
        if oc is None or not isinstance(oc.source_snapshot, dict):
            continue
        if not isinstance(oc.source_snapshot, dict):
            continue
        maybe = oc.source_snapshot.get("payload", oc.source_snapshot)
        opay = maybe if isinstance(maybe, dict) else oc.source_snapshot
        if not isinstance(opay, dict):
            continue
        o_items = opay.get("items", [])
        if not isinstance(o_items, list):
            continue
        o_ids = {
            it["source_id"]
            for it in o_items
            if isinstance(it, dict) and isinstance(it.get("source_id"), int)
        }
        if not o_ids:
            o_ids = {
                it["track_id"]
                for it in o_items
                if isinstance(it, dict) and isinstance(it.get("track_id"), int)
            }
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


def enqueue_review_apply(
    session: Session,
    review_bundle_id: int,
    *,
    idempotency_key: str,
    backup: bool | None = None,
) -> ReviewApplyEnqueued:
    bundle = session.get(ReviewBundle, review_bundle_id)
    if bundle is None:
        raise ReviewInvariantError(f"review bundle {review_bundle_id} not found")
    # REVIEW-CONFLICTS-001: synchronous preflight before 202
    _preflight_for_apply(session, bundle)

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
            run = start_apply_run(session, review_bundle_id, idempotency_key=idempotency_key)
    else:
        run = start_apply_run(session, review_bundle_id, idempotency_key=idempotency_key)

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
