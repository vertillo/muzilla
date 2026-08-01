from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from muzilla.db.models import Job
from muzilla.jobs import queue


def test_enqueue_then_lease(db_session: Session) -> None:
    job = queue.enqueue(db_session, type="scan", payload={"root": "/music"})
    assert job.state == "pending"

    leased = queue.lease_next(db_session, worker_id="w1", lease_seconds=60)
    assert leased is not None
    assert leased.id == job.id
    assert leased.state == "running"
    assert leased.worker_id == "w1"
    assert leased.attempts == 1
    assert leased.lease_until is not None


def test_lease_next_never_returns_same_pending_job_twice(db_session: Session) -> None:
    queue.enqueue(db_session, type="scan", payload={})

    first = queue.lease_next(db_session, worker_id="w1", lease_seconds=60)
    second = queue.lease_next(db_session, worker_id="w2", lease_seconds=60)

    assert first is not None
    assert second is None


def test_lease_next_orders_by_priority_then_created_at(db_session: Session) -> None:
    low = queue.enqueue(db_session, type="scan", payload={}, priority=0)
    high = queue.enqueue(db_session, type="scan", payload={}, priority=10)

    leased = queue.lease_next(db_session, worker_id="w1", lease_seconds=60)
    assert leased is not None
    assert leased.id == high.id
    assert low.state == "pending"


def test_heartbeat_extends_lease(db_session: Session) -> None:
    queue.enqueue(db_session, type="scan", payload={})
    job = queue.lease_next(db_session, worker_id="w1", lease_seconds=60)
    assert job is not None
    original_lease = job.lease_until

    queue.heartbeat(db_session, job.id, lease_seconds=120)
    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.lease_until is not None
    assert original_lease is not None
    assert refreshed.lease_until.replace(tzinfo=UTC) > original_lease.replace(tzinfo=UTC)


def test_mark_succeeded_and_failed(db_session: Session) -> None:
    job1 = queue.enqueue(db_session, type="scan", payload={})
    queue.mark_succeeded(db_session, job1.id, {"scanned": 5})
    db_session.expire_all()
    refreshed1 = db_session.get(Job, job1.id)
    assert refreshed1 is not None
    assert refreshed1.state == "succeeded"
    assert refreshed1.result == {"scanned": 5}

    job2 = queue.enqueue(db_session, type="scan", payload={})
    queue.mark_failed(db_session, job2.id, "boom")
    db_session.expire_all()
    refreshed2 = db_session.get(Job, job2.id)
    assert refreshed2 is not None
    assert refreshed2.state == "failed"
    assert refreshed2.error == "boom"


def test_request_cancel_immediately_cancels_pending_job(db_session: Session) -> None:
    job = queue.enqueue(db_session, type="scan", payload={})
    queue.request_cancel(db_session, job.id)
    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.cancel_requested is True
    assert refreshed.state == "cancelled"


def test_request_cancel_marks_running_job_cancelling(db_session: Session) -> None:
    job = queue.enqueue(db_session, type="scan", payload={})
    leased = queue.lease_next(db_session, worker_id="w1", lease_seconds=60)
    assert leased is not None

    queue.request_cancel(db_session, job.id)

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.cancel_requested is True
    assert refreshed.state == "cancelling"
    events = queue.list_events_after(db_session, job.id, after_seq=0)
    assert events[-1].kind == "state"
    assert events[-1].payload == {"state": "cancelling"}


def test_append_event_seq_monotonic(db_session: Session) -> None:
    job = queue.enqueue(db_session, type="scan", payload={})
    e1 = queue.append_event(db_session, job.id, "progress", {"current": 1})
    e2 = queue.append_event(db_session, job.id, "progress", {"current": 2})
    assert e1.seq == 1
    assert e2.seq == 2


def test_list_events_after(db_session: Session) -> None:
    job = queue.enqueue(db_session, type="scan", payload={})
    queue.append_event(db_session, job.id, "progress", {"current": 1})
    queue.append_event(db_session, job.id, "progress", {"current": 2})
    queue.append_event(db_session, job.id, "progress", {"current": 3})

    events = queue.list_events_after(db_session, job.id, after_seq=1)
    assert [e.seq for e in events] == [2, 3]


def test_list_jobs_cursor_pagination(db_session: Session) -> None:
    for _ in range(5):
        queue.enqueue(db_session, type="scan", payload={})

    page1, cursor = queue.list_jobs(db_session, limit=2)
    assert len(page1) == 2
    assert cursor is not None

    page2, _ = queue.list_jobs(db_session, cursor=cursor, limit=2)
    assert len(page2) == 2
    assert {j.id for j in page1}.isdisjoint({j.id for j in page2})


def test_recover_stuck_jobs_resets_expired_lease(db_session: Session) -> None:
    job = queue.enqueue(db_session, type="scan", payload={})
    leased = queue.lease_next(db_session, worker_id="w1", lease_seconds=60)
    assert leased is not None

    leased.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    recovered = queue.recover_stuck_jobs(db_session)
    assert recovered == 1

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.state == "pending"
    assert refreshed.worker_id is None
    assert refreshed.lease_until is None


def test_recover_stuck_jobs_leaves_active_lease_alone(db_session: Session) -> None:
    queue.enqueue(db_session, type="scan", payload={})
    queue.lease_next(db_session, worker_id="w1", lease_seconds=3600)

    recovered = queue.recover_stuck_jobs(db_session)
    assert recovered == 0
