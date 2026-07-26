from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from muzilla.jobs import queue
from muzilla.services import jobs as jobs_service


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
