from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import (
    ApplyRun,
    Job,
    Operation,
    OperationAttempt,
    ProposalRevision,
    ReviewBundle,
    SourceSnapshot,
    TaskAttempt,
)
from muzilla.domain.reviews import BundleState
from muzilla.services.reviews import (
    NoAcceptedOperationsError,
    OperationDraft,
    ReviewInvariantError,
    ReviewTasksPendingError,
    finish_task_attempt,
    get_review_bundle,
    plan_task_attempt,
    put_revision,
    start_apply_run,
    start_task_attempt,
    transition_bundle,
)


def _snapshot(title: str = "Current title") -> dict[str, object]:
    return {
        "items": [
            {
                "source_type": "track",
                "source_id": 17,
                "path": "/music/example.flac",
                "size_bytes": 1234,
                "mtime_ns": 42,
                "tags": {"title": title},
            }
        ]
    }


def _title_operation(title: str = "Proposed title") -> OperationDraft:
    return OperationDraft(
        kind="set_tag",
        field="title",
        target_type="track",
        target_id=17,
        current_value="Current title",
        proposed_value=title,
        provenance={"provider": "musicbrainz"},
    )


def test_identical_revision_reuses_bundle_revision_snapshot_and_inbox_row(
    db_session: Session,
) -> None:
    first = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(_title_operation(),),
    )
    db_session.commit()
    db_session.expunge_all()  # prove idempotency comes from persisted state

    second = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(_title_operation(),),
    )
    db_session.commit()

    assert second.bundle_id == first.bundle_id
    assert second.revision_id == first.revision_id
    assert second.created_bundle is False
    assert second.created_revision is False
    assert db_session.scalar(select(func.count()).select_from(ReviewBundle)) == 1
    assert db_session.scalar(select(func.count()).select_from(ProposalRevision)) == 1
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 1


def test_changed_proposal_creates_revision_on_same_bundle_and_one_current_revision(
    db_session: Session,
) -> None:
    first = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(_title_operation("First proposal"),),
    )
    transition_bundle(db_session, first.bundle_id, BundleState.READY)
    first_operation = db_session.scalar(
        select(Operation).where(Operation.proposal_revision_id == first.revision_id)
    )
    assert first_operation is not None
    first_operation.decision = "accepted"
    second = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(_title_operation("Second proposal"),),
    )
    db_session.commit()

    assert second.bundle_id == first.bundle_id
    assert second.revision_id != first.revision_id
    assert second.created_revision is True
    revisions = list(
        db_session.scalars(
            select(ProposalRevision)
            .where(ProposalRevision.review_bundle_id == first.bundle_id)
            .order_by(ProposalRevision.revision_no)
        )
    )
    assert [revision.revision_no for revision in revisions] == [1, 2]
    assert [revision.is_current for revision in revisions] == [False, True]
    second_operation = db_session.scalar(
        select(Operation).where(Operation.proposal_revision_id == second.revision_id)
    )
    assert second_operation is not None and second_operation.decision == "pending"
    assert db_session.scalar(select(func.count()).select_from(ReviewBundle)) == 1


def test_visible_bundle_cannot_delete_its_current_revision(db_session: Session) -> None:
    write = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(_title_operation(),),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    db_session.commit()

    revision = db_session.get(ProposalRevision, write.revision_id)
    assert revision is not None
    db_session.delete(revision)

    with pytest.raises(IntegrityError):
        db_session.flush()

    db_session.rollback()
    revision = db_session.get(ProposalRevision, write.revision_id)
    assert revision is not None
    revision.is_current = False

    with pytest.raises(IntegrityError, match="visible review bundle requires a current revision"):
        db_session.flush()


def test_concurrent_identical_revision_converges_on_one_persisted_identity(
    migrated_db: Path,
) -> None:
    factory = create_session_factory(create_db_engine(migrated_db))
    barrier = Barrier(2)

    def write() -> tuple[int, int]:
        with factory() as session:
            barrier.wait()
            result = put_revision(
                session,
                logical_key="track:17",
                title="Review example.flac",
                scope_type="track",
                scope_id=17,
                source_snapshot=_snapshot(),
                operations=(_title_operation(),),
            )
            session.commit()
            return result.bundle_id, result.revision_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        identities = list(executor.map(lambda _index: write(), range(2)))

    assert identities[0] == identities[1]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ReviewBundle)) == 1
        assert session.scalar(select(func.count()).select_from(ProposalRevision)) == 1


def test_snapshot_current_proposal_and_apply_attempt_remain_separate(
    db_session: Session,
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(_title_operation(),),
    )
    operation = db_session.scalar(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    )
    assert operation is not None
    operation.decision = "accepted"
    transition_bundle(db_session, write.bundle_id, BundleState.READY)

    run = start_apply_run(db_session, write.bundle_id, idempotency_key="apply-17")
    db_session.commit()

    snapshot = db_session.get(SourceSnapshot, operation.source_snapshot_id)
    assert snapshot is not None
    items = snapshot.payload["items"]
    assert isinstance(items, list)
    first_item = items[0]
    assert isinstance(first_item, dict)
    tags = first_item["tags"]
    assert isinstance(tags, dict)
    assert tags["title"] == "Current title"
    assert operation.current_value == "Current title"
    assert operation.proposed_value == "Proposed title"
    assert run.state == "pending"
    attempt = db_session.scalar(
        select(OperationAttempt).where(OperationAttempt.apply_run_id == run.id)
    )
    assert attempt is not None
    assert attempt.attempted_value == "Proposed title"


def test_apply_run_is_persistently_idempotent_and_rejects_zero_accepted(
    db_session: Session,
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(_title_operation(),),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)

    with pytest.raises(NoAcceptedOperationsError):
        start_apply_run(db_session, write.bundle_id, idempotency_key="empty")

    operation = db_session.scalar(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    )
    assert operation is not None
    operation.decision = "accepted"
    first = start_apply_run(db_session, write.bundle_id, idempotency_key="same-request")
    second = start_apply_run(db_session, write.bundle_id, idempotency_key="same-request")
    db_session.commit()

    assert second.id == first.id
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 1


@pytest.mark.parametrize(
    ("kind", "item_key", "job_type"),
    [
        ("cover", "track:17", "enrich_art"),
        ("lyrics", "track:17", "enrich_lyrics"),
        ("replaygain", "track:17", "enrich_replaygain"),
    ],
)
def test_worker_claims_and_completes_the_planned_attempt_on_the_same_bundle(
    db_session: Session, kind: str, item_key: str, job_type: str
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(_title_operation(),),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    job = Job(type=job_type, payload={"review_bundle_id": write.bundle_id})
    db_session.add(job)
    db_session.flush()
    planned = plan_task_attempt(
        db_session,
        write.bundle_id,
        kind=kind,
        item_key=item_key,
        job_id=job.id,
    )
    assert planned.state == "pending"

    running = start_task_attempt(
        db_session,
        write.bundle_id,
        kind=kind,
        item_key=item_key,
        job_id=job.id,
    )
    finish_task_attempt(db_session, running, state="succeeded", result={"ready": True})

    detail = get_review_bundle(db_session, write.bundle_id)
    assert running.id == planned.id
    assert detail is not None and detail.id == write.bundle_id
    assert detail.task_attempts[-1].state == "succeeded"
    assert db_session.scalar(select(func.count()).select_from(ReviewBundle)) == 1


@pytest.mark.parametrize("active_state", ["pending", "running"])
@pytest.mark.parametrize(
    "terminal_state", ["not_found", "transient_failure", "permanent_failure", "cancelled"]
)
def test_apply_warns_for_unfinished_tasks_then_freezes_after_terminal_outcome(
    db_session: Session, active_state: str, terminal_state: str
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(_title_operation(),),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    operation = db_session.scalar(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    )
    assert operation is not None
    operation.decision = "accepted"
    task = TaskAttempt(
        review_bundle_id=write.bundle_id,
        proposal_revision_id=write.revision_id,
        kind="lyrics",
        item_key="track:17",
        attempt_no=1,
        state=active_state,
    )
    db_session.add(task)
    db_session.flush()

    with pytest.raises(ReviewTasksPendingError, match="unfinished optional tasks") as warning:
        start_apply_run(db_session, write.bundle_id, idempotency_key="apply-ready")

    assert warning.value.attempts == (("lyrics", "track:17", active_state),)
    assert operation.decision == "accepted"
    bundle = db_session.get(ReviewBundle, write.bundle_id)
    assert bundle is not None and bundle.state == "ready"
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 0

    task.state = terminal_state
    run = start_apply_run(db_session, write.bundle_id, idempotency_key="apply-ready")

    assert run.proposal_revision_id == write.revision_id
    assert bundle.state == "applying"
    assert operation.decision == "accepted"


def test_concurrent_same_apply_idempotency_key_returns_the_persisted_run(
    migrated_db: Path,
) -> None:
    """Two requests may pass the read before either one creates the run."""
    factory = create_session_factory(create_db_engine(migrated_db))
    with factory() as session:
        write = put_revision(
            session,
            logical_key="track:17",
            title="Review example.flac",
            scope_type="track",
            scope_id=17,
            source_snapshot=_snapshot(),
            operations=(_title_operation(),),
        )
        operation = session.scalar(
            select(Operation).where(Operation.proposal_revision_id == write.revision_id)
        )
        assert operation is not None
        operation.decision = "accepted"
        transition_bundle(session, write.bundle_id, BundleState.READY)
        session.commit()
        bundle_id = write.bundle_id

    barrier = Barrier(2)

    def start() -> tuple[str, int | str]:
        with factory() as session:
            try:
                barrier.wait()
                run = start_apply_run(session, bundle_id, idempotency_key="same-request")
                session.commit()
                return "ok", run.id
            except Exception as exc:  # baseline exposes the database uniqueness error
                session.rollback()
                return type(exc).__name__, str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: start(), range(2)))

    assert [outcome[0] for outcome in outcomes] == ["ok", "ok"]
    assert outcomes[0][1] == outcomes[1][1]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ApplyRun)) == 1


def test_write_lyrics_payload_is_structured_in_persistence(db_session: Session) -> None:
    write = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(
            OperationDraft(
                kind="write_lyrics",
                field="lyrics",
                target_type="track",
                target_id=17,
                current_value=None,
                proposed_value={"text": "line one", "synced": True, "provider": "lrclib"},
            ),
        ),
    )
    db_session.commit()

    operation = db_session.scalar(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    )
    assert operation is not None
    assert operation.proposed_value == {
        "text": "line one",
        "synced": True,
        "provider": "lrclib",
    }


def test_write_lyrics_rejects_object_string_before_persistence(db_session: Session) -> None:
    with pytest.raises(ReviewInvariantError, match="write_lyrics proposed_value"):
        put_revision(
            db_session,
            logical_key="track:17",
            title="Review example.flac",
            scope_type="track",
            scope_id=17,
            source_snapshot=_snapshot(),
            operations=(
                OperationDraft(
                    kind="write_lyrics",
                    field="lyrics",
                    target_type="track",
                    target_id=17,
                    current_value=None,
                    proposed_value="[object Object]",
                ),
            ),
        )


def test_write_lyrics_rejects_unstructured_current_value_before_persistence(
    db_session: Session,
) -> None:
    with pytest.raises(ReviewInvariantError, match="write_lyrics current_value"):
        put_revision(
            db_session,
            logical_key="track:17",
            title="Review example.flac",
            scope_type="track",
            scope_id=17,
            source_snapshot=_snapshot(),
            operations=(
                OperationDraft(
                    kind="write_lyrics",
                    field="lyrics",
                    target_type="track",
                    target_id=17,
                    current_value="old lyrics",
                    proposed_value={"text": "line one", "synced": True, "provider": "lrclib"},
                ),
            ),
        )


@pytest.mark.parametrize(
    "operation",
    [
        OperationDraft(
            kind="embed_art",
            field="art",
            target_type="track",
            target_id=17,
            current_value=None,
            proposed_value="not-a-blob-ref",
        ),
        OperationDraft(
            kind="remove_art",
            field="art",
            target_type="track",
            target_id=17,
            current_value=None,
            proposed_value={"blob_id": 4},
        ),
        OperationDraft(
            kind="move_file",
            field="path",
            target_type="track",
            target_id=17,
            current_value=None,
            proposed_value="/music/new.flac",
        ),
        OperationDraft(
            kind="set_replay_gain",
            field="rg_track_gain",
            target_type="track",
            target_id=17,
            current_value=None,
            proposed_value=True,
        ),
    ],
    ids=["embed-art", "remove-art", "move-file", "replay-gain"],
)
def test_typed_operation_rejects_values_incompatible_with_api_contract(
    db_session: Session, operation: OperationDraft
) -> None:
    with pytest.raises(ReviewInvariantError):
        put_revision(
            db_session,
            logical_key="track:17",
            title="Invalid typed operation",
            scope_type="track",
            scope_id=17,
            source_snapshot=_snapshot(),
            operations=(operation,),
        )


@pytest.mark.parametrize(
    "operation",
    [
        OperationDraft(
            kind="embed_art",
            field="art",
            target_type="track",
            target_id=17,
            current_value=None,
            proposed_value={"blob_id": 4},
        ),
        OperationDraft(
            kind="remove_art",
            field="art",
            target_type="track",
            target_id=17,
            current_value={"blob_id": 3},
            proposed_value=None,
        ),
        OperationDraft(
            kind="move_file",
            field="path",
            target_type="track",
            target_id=17,
            current_value="/music/old.flac",
            proposed_value="/music/new.flac",
        ),
        OperationDraft(
            kind="set_replay_gain",
            field="rg_track_gain",
            target_type="track",
            target_id=17,
            current_value=-5.25,
            proposed_value=-4,
        ),
    ],
    ids=["embed-art", "remove-art", "move-file", "replay-gain"],
)
def test_typed_operation_accepts_values_from_api_contract(
    db_session: Session, operation: OperationDraft
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:17",
        title="Valid typed operation",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(operation,),
    )

    db_session.commit()

    persisted = db_session.scalar(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    )
    assert persisted is not None
    assert persisted.current_value == operation.current_value
    assert persisted.proposed_value == operation.proposed_value


@pytest.mark.parametrize(
    ("kind", "current_value", "proposed_value"),
    [
        ("write_lyrics", "old lyrics", {"text": "new", "synced": False, "provider": "x"}),
        ("embed_art", None, "not-a-blob-ref"),
        ("remove_art", None, {"blob_id": 4}),
        ("move_file", None, "/music/new.flac"),
        ("set_replay_gain", None, True),
    ],
    ids=["write-lyrics", "embed-art", "remove-art", "move-file", "replay-gain"],
)
def test_database_rejects_values_incompatible_with_typed_operation_contract(
    db_session: Session,
    kind: str,
    current_value: object | None,
    proposed_value: object | None,
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(_title_operation(),),
    )
    valid_operation = db_session.scalar(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    )
    assert valid_operation is not None
    db_session.commit()

    malformed = Operation(
        proposal_revision_id=write.revision_id,
        source_snapshot_id=valid_operation.source_snapshot_id,
        seq=1,
        kind=kind,
        field="typed-field",
        target_type="track",
        target_id=17,
        current_value=current_value,
        proposed_value=proposed_value,
        decision="pending",
        provenance={},
        validation={},
    )
    db_session.add(malformed)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_database_rejects_malformed_write_lyrics_and_operation_content_updates(
    db_session: Session,
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(_title_operation(),),
    )
    valid_operation = db_session.scalar(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    )
    assert valid_operation is not None
    valid_operation_id = valid_operation.id
    db_session.commit()
    malformed = Operation(
        proposal_revision_id=write.revision_id,
        source_snapshot_id=valid_operation.source_snapshot_id,
        seq=1,
        kind="write_lyrics",
        field="lyrics",
        target_type="track",
        target_id=17,
        current_value=None,
        proposed_value="[object Object]",
        decision="pending",
        provenance={},
        validation={},
    )
    db_session.add(malformed)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    operation = db_session.get(Operation, valid_operation_id)
    assert operation is not None
    operation.proposed_value = "mutated after revision creation"
    with pytest.raises(IntegrityError, match="operation proposal content is immutable"):
        db_session.flush()


def test_database_enforces_state_machine_and_freezes_decisions_during_apply(
    db_session: Session,
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:17",
        title="Review example.flac",
        scope_type="track",
        scope_id=17,
        source_snapshot=_snapshot(),
        operations=(_title_operation(),),
    )
    db_session.commit()
    bundle = db_session.get(ReviewBundle, write.bundle_id)
    assert bundle is not None
    bundle.state = "applied"
    with pytest.raises(IntegrityError, match="invalid review bundle state transition"):
        db_session.flush()
    db_session.rollback()

    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    operation = db_session.scalar(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    )
    assert operation is not None
    operation.decision = "accepted"
    start_apply_run(db_session, write.bundle_id, idempotency_key="frozen")
    db_session.commit()

    operation.decision = "rejected"
    with pytest.raises(IntegrityError, match="operation decision is frozen"):
        db_session.flush()
