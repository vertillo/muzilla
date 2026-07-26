from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.db.models import Job
from muzilla.jobs import queue
from muzilla.jobs.progress import ProgressReporter


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_job(session: Session) -> Job:
    return queue.enqueue(session, type="scan", payload={})


def test_rapid_updates_within_window_produce_one_event(db_session: Session) -> None:
    job = _make_job(db_session)
    clock = _FakeClock()
    reporter = ProgressReporter(db_session, job.id, coalesce_ms=250, clock=clock)

    reporter.update(1, total=10)
    clock.advance(0.05)
    reporter.update(2, total=10)
    clock.advance(0.05)
    reporter.update(3, total=10)

    events = queue.list_events_after(db_session, job.id, after_seq=0)
    assert len(events) == 1
    assert events[0].payload["current"] == 1


def test_updates_spanning_window_produce_two_events(db_session: Session) -> None:
    job = _make_job(db_session)
    clock = _FakeClock()
    reporter = ProgressReporter(db_session, job.id, coalesce_ms=250, clock=clock)

    reporter.update(1, total=10)
    clock.advance(0.3)
    reporter.update(2, total=10)

    events = queue.list_events_after(db_session, job.id, after_seq=0)
    assert len(events) == 2
    assert [e.payload["current"] for e in events] == [1, 2]


def test_flush_emits_pending_update_immediately(db_session: Session) -> None:
    job = _make_job(db_session)
    clock = _FakeClock()
    reporter = ProgressReporter(db_session, job.id, coalesce_ms=250, clock=clock)

    reporter.update(1, total=10)
    clock.advance(0.05)
    reporter.update(2, total=10)  # throttled, held as pending

    events_before_flush = queue.list_events_after(db_session, job.id, after_seq=0)
    assert len(events_before_flush) == 1

    reporter.flush()
    events_after_flush = queue.list_events_after(db_session, job.id, after_seq=0)
    assert len(events_after_flush) == 2
    assert events_after_flush[-1].payload["current"] == 2


def test_flush_with_no_pending_update_is_a_no_op(db_session: Session) -> None:
    job = _make_job(db_session)
    clock = _FakeClock()
    reporter = ProgressReporter(db_session, job.id, coalesce_ms=250, clock=clock)

    reporter.update(1, total=10)
    reporter.flush()

    events = queue.list_events_after(db_session, job.id, after_seq=0)
    assert len(events) == 1


def test_job_progress_columns_update_every_call_even_when_throttled(db_session: Session) -> None:
    job = _make_job(db_session)
    clock = _FakeClock()
    reporter = ProgressReporter(db_session, job.id, coalesce_ms=250, clock=clock)

    reporter.update(1, total=10, message="starting")
    reporter.update(2, total=10, message="still going")  # throttled event, column still updates

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.progress_current == 2
    assert refreshed.progress_message == "still going"


def test_log_is_never_coalesced(db_session: Session) -> None:
    job = _make_job(db_session)
    clock = _FakeClock()
    reporter = ProgressReporter(db_session, job.id, coalesce_ms=250, clock=clock)

    reporter.log("stage: scan starting")
    reporter.log("stage: scan done")

    events = queue.list_events_after(db_session, job.id, after_seq=0)
    assert [e.kind for e in events] == ["log", "log"]
