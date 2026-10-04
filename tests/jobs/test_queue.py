from __future__ import annotations

import fcntl
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import ApplyRun, Job, ReviewBundle, ReviewUndoRun
from muzilla.jobs import queue


def _exit_after_finalizing_file_run(
    database_path: str,
    run_type: str,
    run_id: int,
    result: dict[str, object],
    error: str,
) -> None:
    engine = create_db_engine(Path(database_path))
    factory = create_session_factory(engine)
    with factory() as session:
        if run_type == "apply":
            run = session.get(ApplyRun, run_id)
            assert run is not None
            run.state = "failed"
            run.result = result
            run.error = error
            bundle = session.get(ReviewBundle, run.review_bundle_id)
            assert bundle is not None
            bundle.state = "failed"
            bundle.error = error
        else:
            undo_run = session.get(ReviewUndoRun, run_id)
            assert undo_run is not None
            undo_run.state = "failed"
            undo_run.result = result
            undo_run.error = error
            bundle = session.get(ReviewBundle, undo_run.review_bundle_id)
            assert bundle is not None
            bundle.error = error
        session.commit()
    engine.dispose()
    # Model abrupt process exit after the operation run has committed but
    # before the job supervisor can serialize that outcome onto Job.
    os._exit(0)


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


def test_competing_sessions_claim_one_selected_candidate(db_session: Session) -> None:
    job = queue.enqueue(db_session, type="scan", payload={})
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
    selected = Barrier(2)

    def claim(worker_id: str) -> int | None:
        with factory() as session:
            candidate_id = session.scalar(
                select(Job.id).where(Job.state == "pending").order_by(Job.id).limit(1)
            )
            session.commit()
            assert candidate_id == job.id
            selected.wait(timeout=5)
            leased = queue.lease_next(
                session,
                worker_id=worker_id,
                lease_seconds=60,
                candidate_id=candidate_id,
            )
            return leased.id if leased is not None else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim, f"worker-{index}") for index in range(2)]
        claims = [future.result(timeout=10) for future in futures]

    assert claims.count(job.id) == 1
    assert claims.count(None) == 1


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

    queue.heartbeat(db_session, job.id, worker_id="w1", attempts=job.attempts, lease_seconds=120)
    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.lease_until is not None
    assert original_lease is not None
    assert refreshed.lease_until.replace(tzinfo=UTC) > original_lease.replace(tzinfo=UTC)


def test_stale_worker_cannot_renew_or_complete_reclaimed_lease(db_session: Session) -> None:
    job = queue.enqueue(db_session, type="scan", payload={})
    first = queue.lease_next(db_session, worker_id="worker-a", lease_seconds=60)
    assert first is not None
    stale_attempt = first.attempts

    # Simulate a reclaim after the first owner's lease expires.
    first.worker_id = "worker-b"
    first.attempts += 1
    first.lease_until = datetime.now(UTC) + timedelta(seconds=60)
    db_session.commit()
    owned_lease = first.lease_until

    queue.heartbeat(
        db_session,
        job.id,
        worker_id="worker-a",
        attempts=stale_attempt,
        lease_seconds=120,
    )
    db_session.expire_all()
    renewed = db_session.get(Job, job.id)
    assert renewed is not None
    assert renewed.lease_until is not None
    assert owned_lease is not None
    assert renewed.lease_until.replace(tzinfo=UTC) == owned_lease.replace(tzinfo=UTC)

    completed = queue.mark_succeeded(
        db_session,
        job.id,
        {"state": "succeeded"},
        worker_id="worker-a",
        attempts=stale_attempt,
    )
    assert completed is False
    failed = queue.mark_failed(
        db_session,
        job.id,
        "stale failure",
        worker_id="worker-a",
        attempts=stale_attempt,
    )
    assert failed is False
    cancelled = queue.mark_cancelled(
        db_session,
        job.id,
        worker_id="worker-a",
        attempts=stale_attempt,
    )
    assert cancelled is False

    db_session.expire_all()
    current = db_session.get(Job, job.id)
    assert current is not None
    assert current.state == "running"
    assert current.worker_id == "worker-b"
    assert current.attempts == stale_attempt + 1
    assert current.lease_until is not None
    assert owned_lease is not None
    assert current.lease_until.replace(tzinfo=UTC) == owned_lease.replace(tzinfo=UTC)


def test_mark_succeeded_and_failed(db_session: Session) -> None:
    job1 = queue.enqueue(db_session, type="scan", payload={})
    leased1 = queue.lease_next(db_session, worker_id="w1", lease_seconds=60)
    assert leased1 is not None
    queue.mark_succeeded(
        db_session,
        job1.id,
        {"scanned": 5},
        worker_id="w1",
        attempts=leased1.attempts,
    )
    db_session.expire_all()
    refreshed1 = db_session.get(Job, job1.id)
    assert refreshed1 is not None
    assert refreshed1.state == "succeeded"
    assert refreshed1.result == {"scanned": 5}

    job2 = queue.enqueue(db_session, type="scan", payload={})
    leased2 = queue.lease_next(db_session, worker_id="w1", lease_seconds=60)
    assert leased2 is not None
    queue.mark_failed(db_session, job2.id, "boom", worker_id="w1", attempts=leased2.attempts)
    db_session.expire_all()
    refreshed2 = db_session.get(Job, job2.id)
    assert refreshed2 is not None
    assert refreshed2.state == "failed"
    assert refreshed2.error == "boom"


def test_committed_apply_wins_late_cancel_and_preserves_result(db_session: Session) -> None:
    job = queue.enqueue(db_session, type="apply_review_bundle", payload={})
    leased = queue.lease_next(db_session, worker_id="writer", lease_seconds=60)
    assert leased is not None
    cancel = queue.request_cancel(db_session, job.id)
    assert cancel is not None and cancel.state == "cancelling"

    result = {"apply_run_id": 42, "state": "applied", "files": []}
    assert queue.mark_succeeded(
        db_session,
        job.id,
        result,
        worker_id="writer",
        attempts=leased.attempts,
    )
    db_session.expire_all()
    completed = db_session.get(Job, job.id)
    assert completed is not None
    assert completed.state == "succeeded"
    assert completed.result == result
    states = [
        event.payload["state"]
        for event in queue.list_events_after(db_session, job.id, after_seq=0)
        if event.kind == "state"
    ]
    assert states[-1] == "succeeded"

    # A stale cancel request after completion cannot change the durable outcome.
    assert queue.request_cancel(db_session, job.id) is not None
    db_session.expire_all()
    completed = db_session.get(Job, job.id)
    assert completed is not None and completed.state == "succeeded"
    assert completed.result == result
    assert not completed.cancel_requested


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


def test_recovery_does_not_reclaim_expired_job_while_execution_lock_is_held(
    db_session: Session,
) -> None:
    job = queue.enqueue(db_session, type="scan", payload={})
    leased = queue.lease_next(db_session, worker_id="external-worker", lease_seconds=1)
    assert leased is not None
    leased.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    db_url = getattr(db_session.get_bind(), "url", None)
    assert db_url is not None and db_url.database is not None
    db_path = Path(str(db_url.database))
    lock_dir = db_path.parent / f".{db_path.name}.job-locks"
    lock_dir.mkdir(mode=0o700)
    lock_fd = os.open(lock_dir / f"{job.id}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert queue.recover_stuck_jobs(db_session) == 0
        db_session.expire_all()
        still_running = db_session.get(Job, job.id)
        assert still_running is not None and still_running.state == "running"
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    assert queue.recover_stuck_jobs(db_session) == 1
    db_session.expire_all()
    recovered = db_session.get(Job, job.id)
    assert recovered is not None and recovered.state == "pending"


def _create_recovery_run(session: Session) -> tuple[int, int]:
    from muzilla.db.models import Operation
    from muzilla.domain.reviews import BundleState
    from muzilla.pipeline.reviews import (
        OperationDraft,
        put_revision,
        start_apply_run,
        transition_bundle,
    )

    revision = put_revision(
        session,
        logical_key="track:recovery-test",
        title="Recovery test",
        scope_type="track",
        scope_id=1,
        source_snapshot={"items": []},
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=1,
                current_value="before",
                proposed_value="after",
            ),
        ),
    )
    operation = session.scalar(
        select(Operation).where(Operation.proposal_revision_id == revision.revision_id)
    )
    assert operation is not None
    operation.decision = "accepted"
    transition_bundle(session, revision.bundle_id, BundleState.READY)
    run = start_apply_run(session, revision.bundle_id, idempotency_key="recovery-test")
    return revision.bundle_id, run.id


@pytest.mark.parametrize(
    ("run_type", "durable_result", "run_error", "expected_job_state"),
    [
        pytest.param(
            "apply",
            {
                "state": "failed",
                "atomicity": "review_bundle",
                "files": [{"track_id": 1, "state": "failed", "error": "snapshot mismatch"}],
                "recovery_required": False,
            },
            "snapshot mismatch",
            "failed",
            id="apply-validation-failure",
        ),
        pytest.param(
            "apply",
            {
                "state": "failed",
                "atomicity": "review_bundle",
                "files": [
                    {"track_id": 1, "state": "rolled_back", "error": "rolled back"},
                    {"track_id": 2, "state": "failed", "error": "tag write failed"},
                ],
                "recovery_required": False,
            },
            "tag write failed (rolled back)",
            "failed",
            id="apply-completed-rollback",
        ),
        pytest.param(
            "apply",
            {
                "state": "failed",
                "atomicity": "review_bundle",
                "files": [{"track_id": 1, "state": "applied", "error": "rollback uncertain"}],
                "recovery_required": True,
            },
            "recovery_required: rollback uncertain",
            "failed",
            id="apply-recovery-required",
        ),
        pytest.param(
            "apply",
            {
                "state": "failed",
                "atomicity": "review_bundle",
                "files": [{"track_id": 1, "state": "failed", "error": "cancelled"}],
                "recovery_required": False,
                "cancelled": True,
            },
            "cancelled before file apply",
            "cancelled",
            id="apply-cancelled",
        ),
        pytest.param(
            "undo",
            {
                "state": "failed",
                "atomicity": "review_bundle",
                "files": [{"track_id": 1, "state": "failed", "error": "source drift"}],
                "recovery_required": False,
            },
            "source drift detected for track 1",
            "failed",
            id="undo-validation-failure",
        ),
        pytest.param(
            "undo",
            {
                "state": "failed",
                "atomicity": "review_bundle",
                "files": [
                    {"track_id": 1, "state": "undone", "error": None},
                    {"track_id": 2, "state": "failed", "error": "restore uncertain"},
                ],
                "recovery_required": True,
            },
            "recovery_required: restore uncertain",
            "failed",
            id="undo-recovery-required",
        ),
        pytest.param(
            "undo",
            {
                "state": "failed",
                "atomicity": "review_bundle",
                "files": [{"track_id": 1, "state": "undone", "error": None}],
                "recovery_required": False,
                "cancelled": True,
            },
            "cancelled during undo",
            "cancelled",
            id="undo-cancelled",
        ),
    ],
)
def test_recovery_preserves_finalized_file_run_after_process_exit(
    db_session: Session,
    run_type: str,
    durable_result: dict[str, object],
    run_error: str,
    expected_job_state: str,
) -> None:
    source_apply_run_id: int | None = None
    if run_type == "apply":
        bundle_id, run_id = _create_recovery_run(db_session)
        job_type = "apply_review_bundle"
        payload: dict[str, object] = {"apply_run_id": run_id}
    else:
        bundle_id, source_apply_run_id = _create_recovery_run(db_session)
        source_run = db_session.get(ApplyRun, source_apply_run_id)
        bundle = db_session.get(ReviewBundle, bundle_id)
        assert source_run is not None and bundle is not None
        source_run.state = "applied"
        source_run.result = {
            "state": "applied",
            "atomicity": "review_bundle",
            "files": [],
            "recovery_required": False,
        }
        bundle.state = "applied"
        undo_run = ReviewUndoRun(
            review_bundle_id=bundle_id,
            source_apply_run_id=source_apply_run_id,
            idempotency_key="process-exit-undo",
            state="undoing",
            manifest={},
        )
        db_session.add(undo_run)
        db_session.flush()
        run_id = undo_run.id
        job_type = "undo_review_bundle"
        payload = {"undo_run_id": run_id}

    job = queue.enqueue(db_session, type=job_type, payload=payload)
    leased = queue.lease_next(db_session, worker_id="finalized-then-exited", lease_seconds=60)
    assert leased is not None and leased.id == job.id
    db_session.commit()
    bind = db_session.get_bind()
    database_path = bind.engine.url.database if isinstance(bind, Connection) else bind.url.database
    assert database_path is not None

    child = multiprocessing.get_context("spawn").Process(
        target=_exit_after_finalizing_file_run,
        args=(database_path, run_type, run_id, durable_result, run_error),
    )
    child.start()
    child.join(timeout=15)
    assert not child.is_alive()
    assert child.exitcode == 0

    db_session.expire_all()
    expired_job = db_session.get(Job, job.id)
    assert expired_job is not None
    expired_job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert queue.recover_stuck_jobs(db_session) == 1

    db_session.expire_all()
    recovered_job = db_session.get(Job, job.id)
    assert recovered_job is not None and recovered_job.state == expected_job_state
    assert recovered_job.error == run_error
    if run_type == "apply":
        recovered_run = db_session.get(ApplyRun, run_id)
        assert recovered_run is not None and recovered_run.state == "failed"
        assert recovered_run.result == durable_result
        assert recovered_run.error == run_error
        expected_job_result = {
            "apply_run_id": run_id,
            "review_bundle_id": bundle_id,
            **durable_result,
        }
    else:
        recovered_undo_run = db_session.get(ReviewUndoRun, run_id)
        assert recovered_undo_run is not None and recovered_undo_run.state == "failed"
        assert recovered_undo_run.result == durable_result
        assert recovered_undo_run.error == run_error
        assert source_apply_run_id is not None
        expected_job_result = {
            "undo_run_id": run_id,
            "review_bundle_id": bundle_id,
            "source_apply_run_id": source_apply_run_id,
            **durable_result,
        }
    assert recovered_job.result == expected_job_result


def test_recovery_marks_uncertain_apply_for_explicit_recovery(db_session: Session) -> None:
    from muzilla.db.models import ApplyRun, ReviewBundle

    bundle_id, run_id = _create_recovery_run(db_session)
    job = queue.enqueue(db_session, type="apply_review_bundle", payload={"apply_run_id": run_id})
    leased = queue.lease_next(db_session, worker_id="dead-apply", lease_seconds=1)
    assert leased is not None
    leased.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    assert queue.recover_stuck_jobs(db_session) == 1
    db_session.expire_all()
    recovered_job = db_session.get(Job, job.id)
    recovered_run = db_session.get(ApplyRun, run_id)
    recovered_bundle = db_session.get(ReviewBundle, bundle_id)
    assert recovered_job is not None and recovered_job.state == "failed"
    assert recovered_job.error is not None and "recovery_required" in recovered_job.error
    assert recovered_job.result is not None
    assert recovered_job.result["recovery_required"] is True
    assert recovered_run is not None and recovered_run.state == "failed"
    assert recovered_run.result is not None
    assert recovered_run.result["recovery_required"] is True
    assert recovered_bundle is not None and recovered_bundle.state == "failed"
    assert recovered_bundle.error is not None and "recovery_required" in recovered_bundle.error


def test_recovery_preserves_durable_apply_commit(db_session: Session) -> None:
    from muzilla.db.models import ApplyRun, ReviewBundle

    bundle_id, run_id = _create_recovery_run(db_session)
    run = db_session.get(ApplyRun, run_id)
    bundle = db_session.get(ReviewBundle, bundle_id)
    assert run is not None and bundle is not None
    durable_result: dict[str, object] = {
        "state": "applied",
        "atomicity": "review_bundle",
        "files": [{"track_id": 1, "state": "applied"}],
        "recovery_required": False,
    }
    run.state = "applied"
    run.result = durable_result
    bundle.state = "applied"
    job = queue.enqueue(db_session, type="apply_review_bundle", payload={"apply_run_id": run_id})
    leased = queue.lease_next(db_session, worker_id="dead-worker", lease_seconds=1)
    assert leased is not None
    queue.request_cancel(db_session, job.id)
    leased.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    assert queue.recover_stuck_jobs(db_session) == 1
    db_session.expire_all()
    recovered_job = db_session.get(Job, job.id)
    recovered_run = db_session.get(ApplyRun, run_id)
    assert recovered_job is not None and recovered_job.state == "succeeded"
    assert recovered_job.result == {
        "apply_run_id": run_id,
        "review_bundle_id": bundle_id,
        **durable_result,
    }
    assert not recovered_job.cancel_requested
    assert recovered_run is not None and recovered_run.state == "applied"
    assert recovered_run.result == durable_result


def test_recovery_preserves_durable_undo_commit(db_session: Session) -> None:
    from muzilla.db.models import ApplyRun, ReviewBundle, ReviewUndoRun

    bundle_id, apply_run_id = _create_recovery_run(db_session)
    apply_run = db_session.get(ApplyRun, apply_run_id)
    bundle = db_session.get(ReviewBundle, bundle_id)
    assert apply_run is not None and bundle is not None
    apply_run.state = "applied"
    apply_run.result = {"state": "applied", "files": [], "recovery_required": False}
    bundle.state = "applied"
    undo_run = ReviewUndoRun(
        review_bundle_id=bundle_id,
        source_apply_run_id=apply_run_id,
        idempotency_key="durable-undo",
        state="undone",
        manifest={},
        result={"state": "undone", "atomicity": "review_bundle", "files": []},
    )
    db_session.add(undo_run)
    db_session.flush()
    job = queue.enqueue(db_session, type="undo_review_bundle", payload={"undo_run_id": undo_run.id})
    leased = queue.lease_next(db_session, worker_id="dead-undo", lease_seconds=1)
    assert leased is not None
    queue.request_cancel(db_session, job.id)
    leased.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    assert queue.recover_stuck_jobs(db_session) == 1
    db_session.expire_all()
    recovered_job = db_session.get(Job, job.id)
    recovered_run = db_session.get(ReviewUndoRun, undo_run.id)
    assert recovered_job is not None and recovered_job.state == "succeeded"
    assert recovered_job.result == {
        "undo_run_id": undo_run.id,
        "review_bundle_id": bundle_id,
        "source_apply_run_id": apply_run_id,
        "state": "undone",
        "atomicity": "review_bundle",
        "files": [],
    }
    assert recovered_run is not None and recovered_run.state == "undone"
    assert not recovered_job.cancel_requested


def test_recovery_preserves_apply_and_fails_closed_for_uncertain_undo(
    db_session: Session,
) -> None:
    from muzilla.db.models import ApplyRun, ReviewBundle, ReviewUndoRun

    bundle_id, apply_run_id = _create_recovery_run(db_session)
    enqueued_apply = queue.enqueue(
        db_session, type="apply_review_bundle", payload={"apply_run_id": apply_run_id}
    )
    leased_apply = queue.lease_next(db_session, worker_id="dead-apply", lease_seconds=1)
    assert leased_apply is not None
    leased_apply.lease_until = datetime.now(UTC) - timedelta(seconds=1)

    bundle = db_session.get(ReviewBundle, bundle_id)
    apply_run = db_session.get(ApplyRun, apply_run_id)
    assert bundle is not None and apply_run is not None
    bundle.state = "applied"
    apply_run.state = "applied"
    apply_run.result = {"state": "applied", "files": [], "recovery_required": False}
    undo_run = ReviewUndoRun(
        review_bundle_id=bundle_id,
        source_apply_run_id=apply_run_id,
        idempotency_key="recovery-undo",
        state="undoing",
        manifest={},
    )
    db_session.add(undo_run)
    db_session.flush()
    undo_job = queue.enqueue(
        db_session, type="undo_review_bundle", payload={"undo_run_id": undo_run.id}
    )
    leased_undo = queue.lease_next(db_session, worker_id="dead-undo", lease_seconds=1)
    assert leased_undo is not None and leased_undo.id == undo_job.id
    leased_undo.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    assert queue.recover_stuck_jobs(db_session) == 2
    db_session.expire_all()
    recovered_apply = db_session.get(Job, enqueued_apply.id)
    recovered_undo = db_session.get(Job, undo_job.id)
    recovered_run = db_session.get(ReviewUndoRun, undo_run.id)
    recovered_bundle = db_session.get(ReviewBundle, bundle_id)
    assert recovered_apply is not None and recovered_apply.state == "succeeded"
    assert recovered_undo is not None and recovered_undo.state == "failed"
    assert recovered_undo.result is not None
    assert recovered_undo.result["recovery_required"] is True
    assert recovered_run is not None and recovered_run.state == "failed"
    assert recovered_run.result is not None
    assert recovered_run.result["recovery_required"] is True
    assert recovered_bundle is not None
    assert recovered_bundle.state == "applied"
    assert recovered_bundle.error is not None and "recovery_required" in recovered_bundle.error


def test_concurrent_recovery_sessions_reconcile_an_expired_job_once(
    db_session: Session,
) -> None:
    job = queue.enqueue(db_session, type="scan", payload={})
    leased = queue.lease_next(db_session, worker_id="dead-worker", lease_seconds=1)
    assert leased is not None
    leased.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
    selected = Barrier(2)

    def recover() -> int:
        with factory() as session:
            selected.wait(timeout=5)
            return queue.recover_stuck_jobs(session)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=10)
            for future in (executor.submit(recover), executor.submit(recover))
        ]

    assert sum(results) == 1
    db_session.expire_all()
    recovered = db_session.get(Job, job.id)
    assert recovered is not None and recovered.state == "pending"


def test_recover_stuck_jobs_leaves_active_lease_alone(db_session: Session) -> None:
    queue.enqueue(db_session, type="scan", payload={})
    queue.lease_next(db_session, worker_id="w1", lease_seconds=3600)

    recovered = queue.recover_stuck_jobs(db_session)
    assert recovered == 0
