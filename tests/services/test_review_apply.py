from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.changes.writer import capture_file_guard
from muzilla.db.models import ApplyRun, Job, Operation, ReviewBundle, Track
from muzilla.domain.metadata import tag_hash
from muzilla.domain.reviews import BundleState
from muzilla.services.review_apply import enqueue_review_apply
from muzilla.services.reviews import OperationDraft, put_revision, transition_bundle
from muzilla.tags.reader import read_track


def _ready_review(
    session: Session,
    tmp_path: Path,
    *,
    include_physical_guard: bool = True,
    include_target: bool = True,
) -> int:
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    path = library / "track.mp3"
    shutil.copy(Path("tests/fixtures/audio/silence.mp3"), path)
    metadata = read_track(path)
    stat = path.stat()
    now = datetime.now(UTC)
    track = Track(
        path=str(path),
        filename=path.name,
        ext=path.suffix,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        title=metadata.title or "Before",
        artist=metadata.artist or "Artist",
        tag_hash=tag_hash(metadata),
        first_seen_at=now,
        last_scanned_at=now,
    )
    session.add(track)
    session.flush()
    source_item: dict[str, object] = {
        "source_type": "track",
        "source_id": track.id,
        "path": track.path,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "tag_hash": track.tag_hash,
    }
    if include_physical_guard:
        source_item["physical_guard"] = capture_file_guard(path, tmp_path)
    write = put_revision(
        session,
        logical_key=f"track:{track.id}",
        title="Review enqueue",
        scope_type="track",
        scope_id=track.id,
        source_snapshot={"items": [source_item] if include_target else []},
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value=track.title,
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
    db_session: Session, tmp_path: Path
) -> None:
    review_id = _ready_review(db_session, tmp_path)

    first = enqueue_review_apply(
        db_session,
        review_id,
        idempotency_key="same-request",
        library_root=tmp_path,
        backup=True,
    )
    second = enqueue_review_apply(
        db_session,
        review_id,
        idempotency_key="same-request",
        library_root=tmp_path,
        backup=True,
    )

    assert second == first
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1
    job = db_session.get(Job, first.job_id)
    assert job is not None
    assert job.type == "apply_review_bundle"
    assert job.payload == {"apply_run_id": first.apply_run_id, "backup": True}


def test_partial_run_retry_reuses_manifest_and_enqueues_only_one_new_job(
    db_session: Session, tmp_path: Path
) -> None:
    review_id = _ready_review(db_session, tmp_path)
    first = enqueue_review_apply(
        db_session, review_id, idempotency_key="initial", library_root=tmp_path
    )
    run = db_session.get(ApplyRun, first.apply_run_id)
    bundle = db_session.get(ReviewBundle, review_id)
    first_job = db_session.get(Job, first.job_id)
    assert run is not None and bundle is not None and first_job is not None
    run.state = "partially_applied"
    bundle.state = "partially_applied"
    first_job.state = "succeeded"
    db_session.commit()

    retry = enqueue_review_apply(
        db_session, review_id, idempotency_key="retry", library_root=tmp_path
    )
    duplicate = enqueue_review_apply(
        db_session, review_id, idempotency_key="retry", library_root=tmp_path
    )

    assert retry.apply_run_id == first.apply_run_id
    assert duplicate == retry
    assert retry.job_id != first.job_id
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(Job)) == 2


@pytest.mark.parametrize(
    ("include_target", "include_physical_guard"),
    [(True, False), (False, False)],
    ids=["missing-physical-guard", "omitted-target"],
)
def test_start_apply_rejects_incomplete_physical_target_snapshot_before_run(
    db_session: Session,
    tmp_path: Path,
    include_target: bool,
    include_physical_guard: bool,
) -> None:
    from muzilla.pipeline.reviews import ReviewInvariantError, start_apply_run

    review_id = _ready_review(
        db_session,
        tmp_path,
        include_physical_guard=include_physical_guard,
        include_target=include_target,
    )

    with pytest.raises(ReviewInvariantError, match="stale_source"):
        start_apply_run(db_session, review_id, idempotency_key="missing-source")

    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 0
