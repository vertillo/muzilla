from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from muzilla.db.models import ImportSession, ReviewBundle, TaskAttempt
from muzilla.domain.reviews import BundleState
from muzilla.jobs import queue
from muzilla.services import jobs as jobs_service
from muzilla.services.reviews import (
    OperationDraft,
    plan_task_attempt,
    put_revision,
    transition_bundle,
)


def test_enqueue_scan_and_get_job(db_session: Session) -> None:
    summary = jobs_service.enqueue_scan(db_session, "/music")
    assert summary.type == "scan"
    assert summary.state == "pending"

    detail = jobs_service.get_job(db_session, summary.id)
    assert detail is not None
    assert detail.payload == {"root": "/music"}


def test_get_job_missing_returns_none(db_session: Session) -> None:
    assert jobs_service.get_job(db_session, 99999) is None


def test_list_jobs_cursor_pagination(db_session: Session) -> None:
    for _ in range(3):
        jobs_service.enqueue_scan(db_session, "/music")

    page = jobs_service.list_jobs(db_session, limit=2)
    assert len(page.items) == 2
    assert page.next_cursor is not None


def test_list_job_events(db_session: Session) -> None:
    summary = jobs_service.enqueue_scan(db_session, "/music")
    queue.append_event(db_session, summary.id, "progress", {"current": 1})
    queue.append_event(db_session, summary.id, "progress", {"current": 2})

    events = jobs_service.list_job_events(db_session, summary.id, after=0)
    assert [e.seq for e in events] == [1, 2]


def test_request_job_cancel_sets_flag(db_session: Session) -> None:
    summary = jobs_service.enqueue_scan(db_session, "/music")
    detail = jobs_service.request_job_cancel(db_session, summary.id)
    assert detail.id == summary.id

    job = queue.get_job(db_session, summary.id)
    assert job is not None
    assert job.cancel_requested is True


def test_request_job_cancel_missing_raises(db_session: Session) -> None:
    with pytest.raises(ValueError, match="not found"):
        jobs_service.request_job_cancel(db_session, 99999)


def test_recover_stuck_jobs(db_session: Session) -> None:
    job = queue.enqueue(db_session, type="scan", payload={})
    leased = queue.lease_next(db_session, worker_id="w1", lease_seconds=60)
    assert leased is not None
    leased.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    recovered = jobs_service.recover_stuck_jobs(db_session)
    assert recovered == 1

    db_session.expire_all()
    refreshed = queue.get_job(db_session, job.id)
    assert refreshed is not None
    assert refreshed.state == "pending"


def test_request_job_cancel_pending_import_propagates_busy_exhaustion(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pending import cancel must not silently succeed when ImportSession commit is busy-exhausted."""
    from muzilla.services import imports as imports_service

    summary = imports_service.start_import(db_session, "/tmp/lib-pending-cancel")
    job_id = summary.job_id
    assert job_id is not None
    job = queue.get_job(db_session, job_id)
    assert job is not None and job.state == "pending"
    assert job.type == "import"

    original_commit = db_session.commit

    def busy_commit() -> None:
        # Only fail the ImportSession transition (second phase). The initial
        # queue.request_cancel commit has no dirty ImportSession with
        # state=="cancelled", so it succeeds; the follow-up ImportSession
        # commit does and must be retried then surfaced.
        for obj in list(db_session.dirty) + list(db_session.new):
            if isinstance(obj, ImportSession) and getattr(obj, "state", None) == "cancelled":
                raise OperationalError("SELECT 1", {}, Exception("database is locked"))
        return original_commit()

    monkeypatch.setattr(db_session, "commit", busy_commit)
    monkeypatch.setattr("muzilla.services.jobs.time.sleep", lambda *_: None)

    with pytest.raises(OperationalError, match="database is locked"):
        jobs_service.request_job_cancel(db_session, job_id)

    # Job was already marked cancelled durably in the first phase, but
    # ImportSession must not appear cancelled to the caller that received
    # an error — a successful return would have been a silent divergence.
    db_session.rollback()
    refreshed_job = queue.get_job(db_session, job_id)
    assert refreshed_job is not None and refreshed_job.state == "cancelled"
    sess = db_session.get(ImportSession, summary.id)
    assert sess is not None and sess.state != "cancelled"


def test_request_job_cancel_pending_import_propagates_non_busy_error(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from muzilla.services import imports as imports_service

    summary = imports_service.start_import(db_session, "/tmp/lib-pending-cancel-nonbusy")
    job_id = summary.job_id
    assert job_id is not None

    original_commit = db_session.commit

    def failing_commit() -> None:
        for obj in list(db_session.dirty) + list(db_session.new):
            if isinstance(obj, ImportSession) and getattr(obj, "state", None) == "cancelled":
                raise OperationalError("SELECT 1", {}, Exception("constraint failed"))
        return original_commit()

    monkeypatch.setattr(db_session, "commit", failing_commit)

    with pytest.raises(OperationalError, match="constraint failed"):
        jobs_service.request_job_cancel(db_session, job_id)


def test_failed_review_job_terminalizes_attempt_on_the_same_bundle(
    db_session: Session,
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:71",
        title="Review failed task",
        scope_type="track",
        scope_id=71,
        source_snapshot={"items": [{"source_type": "track", "source_id": 71}]},
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=71,
                current_value="Old",
                proposed_value="New",
            ),
        ),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    job = queue.enqueue(
        db_session,
        type="enrich_lyrics",
        payload={"review_bundle_id": write.bundle_id},
        commit=False,
    )
    attempt = plan_task_attempt(
        db_session,
        write.bundle_id,
        kind="lyrics",
        item_key="track:71",
        job_id=job.id,
    )
    db_session.commit()

    queue.mark_failed(db_session, job.id, "worker crashed")

    db_session.expire_all()
    failed = db_session.get(TaskAttempt, attempt.id)
    bundle = db_session.get(ReviewBundle, write.bundle_id)
    assert failed is not None and failed.state == "transient_failure"
    assert failed.error == "worker crashed"
    assert bundle is not None and bundle.state == "needs_attention"
    assert db_session.query(ReviewBundle).count() == 1
