from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.db.models import ApplyRun, Job, Operation, ReviewBundle
from muzilla.domain.reviews import BundleState
from muzilla.services.review_apply import enqueue_review_apply
from muzilla.services.reviews import OperationDraft, put_revision, transition_bundle


def _ready_review(session: Session) -> int:
    write = put_revision(
        session,
        logical_key="track:91",
        title="Review enqueue",
        scope_type="track",
        scope_id=91,
        source_snapshot={
            "items": [
                {
                    "source_type": "track",
                    "source_id": 91,
                    "path": "/music/example.mp3",
                    "size_bytes": 10,
                    "mtime_ns": 20,
                    "tag_hash": "hash",
                }
            ]
        },
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=91,
                current_value="Before",
                proposed_value="After",
            ),
        ),
    )
    operation = session.scalar(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    )
    assert operation is not None
    operation.decision = "accepted"
    transition_bundle(session, write.bundle_id, BundleState.READY)
    session.commit()
    return write.bundle_id


def test_enqueue_is_persistently_idempotent_and_records_run_on_job(
    db_session: Session,
) -> None:
    review_id = _ready_review(db_session)

    first = enqueue_review_apply(
        db_session, review_id, idempotency_key="same-request", backup=True
    )
    second = enqueue_review_apply(
        db_session, review_id, idempotency_key="same-request", backup=True
    )

    assert second == first
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1
    job = db_session.get(Job, first.job_id)
    assert job is not None
    assert job.type == "apply_review_bundle"
    assert job.payload == {"apply_run_id": first.apply_run_id, "backup": True}


def test_partial_run_retry_reuses_manifest_and_enqueues_only_one_new_job(
    db_session: Session,
) -> None:
    review_id = _ready_review(db_session)
    first = enqueue_review_apply(db_session, review_id, idempotency_key="initial")
    run = db_session.get(ApplyRun, first.apply_run_id)
    bundle = db_session.get(ReviewBundle, review_id)
    first_job = db_session.get(Job, first.job_id)
    assert run is not None and bundle is not None and first_job is not None
    run.state = "partially_applied"
    bundle.state = "partially_applied"
    first_job.state = "succeeded"
    db_session.commit()

    retry = enqueue_review_apply(db_session, review_id, idempotency_key="retry")
    duplicate = enqueue_review_apply(db_session, review_id, idempotency_key="retry")

    assert retry.apply_run_id == first.apply_run_id
    assert duplicate == retry
    assert retry.job_id != first.job_id
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(Job)) == 2
