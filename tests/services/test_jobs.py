from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from muzilla.db.models import ReviewBundle, TaskAttempt
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
