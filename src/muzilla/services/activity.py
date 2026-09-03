"""Activity service: user-action aggregation for the Activity page.

Groups work by user action (scan/import, Apply, Undo, duplicate analysis),
exposes outcome/progress/cancellation, and keeps job/event/attempt
details behind diagnostics. System jobs (retention_sweep) stay hidden
by default. Minimal read model, no writes, no filesystem access.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from muzilla.db.models import ApplyRun, ImportSession, Job, ReviewBundle, ReviewUndoRun


@dataclass(frozen=True, slots=True)
class ActivityItem:
    id: str
    kind: str  # import | scan | apply | undo | duplicate_analysis
    title: str
    state: str
    created_at: datetime
    updated_at: datetime
    job_id: int | None
    import_session_id: int | None
    review_bundle_id: int | None
    apply_run_id: int | None
    undo_run_id: int | None
    progress_current: int | None
    progress_total: int | None
    progress_message: str | None
    error: str | None
    result: dict[str, object] | None
    cancellable: bool


@dataclass(frozen=True, slots=True)
class ActivityPage:
    items: tuple[ActivityItem, ...]
    next_cursor: str | None


def _import_state_to_activity(state: str) -> str:
    if state in ("scanning", "fingerprinting", "grouping", "matching"):
        return "running"
    if state in ("reviewing", "completed"):
        return "succeeded"
    if state in ("failed",):
        return "failed"
    if state in ("cancelled",):
        return "cancelled"
    if state in ("pending",):
        return "pending"
    return state


def _ensure_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def encode_activity_cursor(created_at: datetime, item_id: str) -> str:
    raw = json.dumps([_ensure_aware(created_at).isoformat(), item_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_activity_cursor(cursor: str) -> tuple[datetime, str] | None:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if not isinstance(data, list) or len(data) != 2:
            return None
        ts_str, item_id = data
        if not isinstance(ts_str, str) or not isinstance(item_id, str):
            return None
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (dt, item_id)
    except Exception:
        return None


def _apply_state_to_activity(state: str) -> str:
    if state == "applying":
        return "running"
    if state == "applied":
        return "succeeded"
    if state == "failed":
        return "failed"
    if state in ("pending",):
        return "pending"
    if state in ("partially_applied",):
        return "failed"
    return state


def _undo_state_to_activity(state: str) -> str:
    if state == "undoing":
        return "running"
    if state == "undone":
        return "succeeded"
    if state == "failed":
        return "failed"
    if state in ("pending",):
        return "pending"
    if state in ("partially_undone",):
        return "failed"
    return state


def _job_cancellable(job: Job | None) -> bool:
    return job is not None and job.state in ("pending", "running")


def _job_progress(job: Job | None) -> tuple[int | None, int | None, str | None]:
    if job is None:
        return None, None, None
    return job.progress_current, job.progress_total, job.progress_message


def _from_import(session_row: ImportSession, job: Job | None) -> ActivityItem:
    # Visibly prioritize job cancelling/cancelled over session state so
    # cooperative cancellation is immediately reflected in the activity feed.
    if job is not None and job.state == "cancelling":
        state = "cancelling"
    elif job is not None and job.state == "cancelled":
        state = "cancelled"
    else:
        state = _import_state_to_activity(session_row.state)
    # progress from tasks: done vs total
    total = len(session_row.tasks)
    done = sum(1 for t in session_row.tasks if t.state == "done")
    running_task = next((t for t in session_row.tasks if t.state == "running"), None)
    msg = f"stage: {running_task.stage}" if running_task else None
    if state in ("running", "cancelling"):
        pc, pt, pm = done, total, msg or f"{done}/{total} stages"
        if job is not None and job.progress_total is not None:
            pc, pt, pm = job.progress_current, job.progress_total, job.progress_message  # type: ignore[assignment]
    elif state == "succeeded":
        pc, pt, pm = total, total, "importazione completata"
    else:
        pc, pt, pm = None, None, None
    return ActivityItem(
        id=f"import:{session_row.id}",
        kind="import",
        title=f"Importazione {session_row.library_root}",
        state=state,
        created_at=session_row.created_at,
        updated_at=session_row.updated_at,
        job_id=session_row.job_id,
        import_session_id=session_row.id,
        review_bundle_id=None,
        apply_run_id=None,
        undo_run_id=None,
        progress_current=pc,
        progress_total=pt,
        progress_message=pm,
        error=session_row.error,
        result=dict(session_row.stats) if session_row.stats else None,
        cancellable=_job_cancellable(job),
    )


def _from_scan(job: Job) -> ActivityItem:
    title_map = {
        "scan": f"Scansione cartella {job.payload.get('root', '')}".strip(),
        "rescan_track": f"Rilettura file #{job.payload.get('track_id', '')}".strip(),
        "analyze_track": f"Analisi file #{job.payload.get('track_id', '')}".strip(),
    }
    raw_title = title_map.get(job.type, job.type)
    # human label without exposing raw type as primary; keep short
    title = raw_title or "Scansione"
    pc, pt, pm = _job_progress(job)
    return ActivityItem(
        id=f"job:{job.id}",
        kind="scan",
        title=title,
        state=job.state,
        created_at=job.created_at,
        updated_at=job.updated_at,
        job_id=job.id,
        import_session_id=None,
        review_bundle_id=None,
        apply_run_id=None,
        undo_run_id=None,
        progress_current=pc,
        progress_total=pt,
        progress_message=pm,
        error=job.error,
        result=dict(job.result) if job.result else None,
        cancellable=_job_cancellable(job),
    )


def _from_retention(job: Job) -> ActivityItem:
    pc, pt, pm = _job_progress(job)
    return ActivityItem(
        id=f"job:{job.id}",
        kind="scan",
        title="Pulizia cronologia di annullamento",
        state=job.state,
        created_at=job.created_at,
        updated_at=job.updated_at,
        job_id=job.id,
        import_session_id=None,
        review_bundle_id=None,
        apply_run_id=None,
        undo_run_id=None,
        progress_current=pc,
        progress_total=pt,
        progress_message=pm,
        error=job.error,
        result=dict(job.result) if job.result else None,
        cancellable=_job_cancellable(job),
    )


def _from_duplicate(job: Job) -> ActivityItem:
    pc, pt, pm = _job_progress(job)
    return ActivityItem(
        id=f"job:{job.id}",
        kind="duplicate_analysis",
        title="Analisi duplicati",
        state=job.state,
        created_at=job.created_at,
        updated_at=job.updated_at,
        job_id=job.id,
        import_session_id=None,
        review_bundle_id=None,
        apply_run_id=None,
        undo_run_id=None,
        progress_current=pc,
        progress_total=pt,
        progress_message=pm,
        error=job.error,
        result=dict(job.result) if job.result else None,
        cancellable=_job_cancellable(job),
    )


def _from_apply(run: ApplyRun, bundle: ReviewBundle | None, job: Job | None) -> ActivityItem:
    if job is not None and job.state == "cancelling":
        state = "cancelling"
    elif job is not None and job.state == "cancelled":
        state = "cancelled"
    else:
        state = _apply_state_to_activity(run.state)
    title = f"Applicazione revisione #{run.review_bundle_id}"
    if bundle is not None and bundle.title:
        title = f"Applicazione: {bundle.title}"
    pc, pt, pm = _job_progress(job)
    if state in ("running", "cancelling") and pc is None:
        pc, pt, pm = 0, 1, "applicazione in corso"
    return ActivityItem(
        id=f"apply:{run.id}",
        kind="apply",
        title=title,
        state=state,
        created_at=run.created_at,
        updated_at=run.updated_at,
        job_id=job.id if job else None,
        import_session_id=None,
        review_bundle_id=run.review_bundle_id,
        apply_run_id=run.id,
        undo_run_id=None,
        progress_current=pc,
        progress_total=pt,
        progress_message=pm,
        error=run.error,
        result=dict(run.result) if run.result else None,
        cancellable=_job_cancellable(job),
    )


def _from_undo(run: ReviewUndoRun, bundle: ReviewBundle | None, job: Job | None) -> ActivityItem:
    if job is not None and job.state == "cancelling":
        state = "cancelling"
    elif job is not None and job.state == "cancelled":
        state = "cancelled"
    else:
        state = _undo_state_to_activity(run.state)
    title = f"Annullamento revisione #{run.review_bundle_id}"
    if bundle is not None and bundle.title:
        title = f"Annullamento: {bundle.title}"
    pc, pt, pm = _job_progress(job)
    if state in ("running", "cancelling") and pc is None:
        pc, pt, pm = 0, 1, "ripristino in corso"
    return ActivityItem(
        id=f"undo:{run.id}",
        kind="undo",
        title=title,
        state=state,
        created_at=run.created_at,
        updated_at=run.updated_at,
        job_id=job.id if job else None,
        import_session_id=None,
        review_bundle_id=run.review_bundle_id,
        apply_run_id=run.source_apply_run_id,
        undo_run_id=run.id,
        progress_current=pc,
        progress_total=pt,
        progress_message=pm,
        error=run.error,
        result=dict(run.result) if run.result else None,
        cancellable=_job_cancellable(job),
    )


def _resolve_job_for_run(session: Session, manifest: dict[str, object]) -> Job | None:
    raw = manifest.get("job_ids")
    if not isinstance(raw, list) or not raw:
        return None
    ids = [v for v in raw if isinstance(v, int)]
    if not ids:
        return None
    # last job is most recent
    last_id = ids[-1]
    return session.get(Job, last_id)


def list_activity(
    session: Session,
    *,
    cursor: str | None = None,
    limit: int = 50,
    include_system: bool = False,
) -> ActivityPage:
    limit = max(1, min(limit, 100))
    cursor_tuple: tuple[datetime, str] | None = None
    if cursor and cursor.strip():
        cursor_tuple = decode_activity_cursor(cursor.strip())
        # Invalid cursor (e.g. stale offset) is treated as start; do not error.

    # Database/source-level keyset merge: each source is filtered by the
    # global (created_at, item_id) cursor at the DB layer and limited to
    # limit+1, so >300 records remain reachable without in-memory truncation.
    per_source_limit = limit + 1
    c_dt: datetime | None = cursor_tuple[0] if cursor_tuple is not None else None
    c_id: str | None = cursor_tuple[1] if cursor_tuple is not None else None

    def _import_filter():  # type: ignore[no-untyped-def]
        if cursor_tuple is None or c_dt is None or c_id is None:
            return None
        expr = func.printf("import:%d", ImportSession.id)
        return or_(
            ImportSession.created_at < c_dt, and_(ImportSession.created_at == c_dt, expr < c_id)
        )

    def _apply_filter():  # type: ignore[no-untyped-def]
        if cursor_tuple is None or c_dt is None or c_id is None:
            return None
        expr = func.printf("apply:%d", ApplyRun.id)
        return or_(ApplyRun.created_at < c_dt, and_(ApplyRun.created_at == c_dt, expr < c_id))

    def _undo_filter():  # type: ignore[no-untyped-def]
        if cursor_tuple is None or c_dt is None or c_id is None:
            return None
        expr = func.printf("undo:%d", ReviewUndoRun.id)
        return or_(
            ReviewUndoRun.created_at < c_dt, and_(ReviewUndoRun.created_at == c_dt, expr < c_id)
        )

    def _job_filter():  # type: ignore[no-untyped-def]
        if cursor_tuple is None or c_dt is None or c_id is None:
            return None
        expr = func.printf("job:%d", Job.id)
        return or_(Job.created_at < c_dt, and_(Job.created_at == c_dt, expr < c_id))

    import_stmt = select(ImportSession).order_by(
        ImportSession.created_at.desc(),
        func.printf("import:%d", ImportSession.id).desc(),
    )
    imp_f = _import_filter()  # type: ignore[no-untyped-call]
    if imp_f is not None:
        import_stmt = import_stmt.where(imp_f)
    import_stmt = import_stmt.limit(per_source_limit)
    imports = list(session.scalars(import_stmt))

    apply_stmt = select(ApplyRun).order_by(
        ApplyRun.created_at.desc(),
        func.printf("apply:%d", ApplyRun.id).desc(),
    )
    app_f = _apply_filter()  # type: ignore[no-untyped-call]
    if app_f is not None:
        apply_stmt = apply_stmt.where(app_f)
    apply_stmt = apply_stmt.limit(per_source_limit)
    apply_runs = list(session.scalars(apply_stmt))

    undo_stmt = select(ReviewUndoRun).order_by(
        ReviewUndoRun.created_at.desc(),
        func.printf("undo:%d", ReviewUndoRun.id).desc(),
    )
    undo_f = _undo_filter()  # type: ignore[no-untyped-call]
    if undo_f is not None:
        undo_stmt = undo_stmt.where(undo_f)
    undo_stmt = undo_stmt.limit(per_source_limit)
    undo_runs = list(session.scalars(undo_stmt))

    # Jobs for scan/duplicate: include only user-action jobs not already covered by sessions/runs
    job_filter = _job_filter()  # type: ignore[no-untyped-call]
    stmt = select(Job).where(
        Job.type.in_(("scan", "rescan_track", "analyze_track", "detect_duplicates"))
    )
    if job_filter is not None:
        stmt = stmt.where(job_filter)
    stmt = stmt.order_by(Job.created_at.desc(), func.printf("job:%d", Job.id).desc()).limit(
        per_source_limit
    )
    standalone_jobs = list(session.scalars(stmt))
    retention_jobs: list[Job] = []
    if include_system:
        ret_stmt = select(Job).where(Job.type == "retention_sweep")
        if job_filter is not None:
            ret_stmt = ret_stmt.where(job_filter)
        ret_stmt = ret_stmt.order_by(
            Job.created_at.desc(), func.printf("job:%d", Job.id).desc()
        ).limit(per_source_limit)
        retention_jobs = list(session.scalars(ret_stmt))

    # Exclude scan jobs that are import orchestrator duplicates? The standalone import job type is 'import'
    # which we already represent via ImportSession, so excluding it from jobs is correct.

    # Build items
    items: list[ActivityItem] = []
    # cache jobs for import session cancellable check and for apply/undo
    job_by_id: dict[int, Job] = {}
    # populate job_by_id from standalone_jobs and also fetch jobs referenced by sessions/runs if needed
    for j in standalone_jobs:
        job_by_id[j.id] = j
    # For imports, ensure job loaded
    for sess in imports:
        job = None
        if sess.job_id is not None:
            job = job_by_id.get(sess.job_id) or session.get(Job, sess.job_id)
            if job is not None:
                job_by_id[job.id] = job
        items.append(_from_import(sess, job))
    for job in standalone_jobs:
        if job.type == "detect_duplicates":
            items.append(_from_duplicate(job))
        else:
            items.append(_from_scan(job))
    for job in retention_jobs:
        items.append(_from_retention(job))
    for run in apply_runs:
        bundle = session.get(ReviewBundle, run.review_bundle_id)
        job = (
            _resolve_job_for_run(session, run.manifest) if isinstance(run.manifest, dict) else None
        )
        if job is not None:
            job_by_id[job.id] = job
        items.append(_from_apply(run, bundle, job))
    for run_undo in undo_runs:
        bundle = session.get(ReviewBundle, run_undo.review_bundle_id)
        job = (
            _resolve_job_for_run(session, run_undo.manifest)
            if isinstance(run_undo.manifest, dict)
            else None
        )
        if job is not None:
            job_by_id[job.id] = job
        items.append(_from_undo(run_undo, bundle, job))

    # Sort by created_at desc, id string desc for stability (keyset)
    items.sort(key=lambda x: (_ensure_aware(x.created_at), x.id), reverse=True)

    if cursor_tuple is not None:
        c_dt, c_id = cursor_tuple
        filtered = [it for it in items if (_ensure_aware(it.created_at), it.id) < (c_dt, c_id)]
    else:
        filtered = items

    sliced = filtered[: limit + 1]
    has_more = len(sliced) > limit
    page_items = sliced[:limit]
    next_cursor = (
        encode_activity_cursor(page_items[-1].created_at, page_items[-1].id)
        if has_more and page_items
        else None
    )
    return ActivityPage(items=tuple(page_items), next_cursor=next_cursor)
