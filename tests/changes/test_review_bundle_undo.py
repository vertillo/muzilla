from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.changes.applier import ApplyResult
from muzilla.changes.bundle_applier import apply_review_run
from muzilla.changes.bundle_undo import apply_review_undo_run
from muzilla.db.models import (
    ChangeSet,
    Job,
    Operation,
    ReviewUndoRun,
    Track,
)
from muzilla.domain.reviews import BundleState
from muzilla.pipeline.reviews import (
    OperationDraft,
    put_revision,
    start_apply_run,
    transition_bundle,
)
from muzilla.pipeline.scan import scan_library
from muzilla.services.review_undo import ReviewUndoError, enqueue_review_undo
from muzilla.tags.reader import read_track
from muzilla.tags.writer import write_fields

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _applied_run(
    session: Session, tmp_path: Path, names: tuple[str, ...] = ("silence.mp3",)
) -> tuple[Path, list[Track], int, dict[int, str]]:
    library = tmp_path / "library"
    library.mkdir()
    for name in names:
        shutil.copy(FIXTURES / name, library / name)
    scan_library(session, library)
    session.commit()
    tracks = list(session.scalars(select(Track).order_by(Track.filename)))
    original_titles = {track.id: track.title for track in tracks}
    write = put_revision(
        session,
        logical_key="undo:" + ":".join(str(track.id) for track in tracks),
        title="Persistent undo",
        scope_type="group" if len(tracks) > 1 else "track",
        scope_id=None if len(tracks) > 1 else tracks[0].id,
        source_snapshot={
            "items": [
                {
                    "source_type": "track",
                    "source_id": track.id,
                    "path": track.path,
                    "filename": track.filename,
                    "size_bytes": track.size_bytes,
                    "mtime_ns": track.mtime_ns,
                    "tag_hash": track.tag_hash,
                }
                for track in tracks
            ]
        },
        operations=tuple(
            operation
            for track in tracks
            for operation in (
                OperationDraft(
                    kind="set_tag",
                    field="title",
                    target_type="track",
                    target_id=track.id,
                    current_value=track.title,
                    proposed_value=f"Applied {track.id}",
                ),
                OperationDraft(
                    kind="move_file",
                    field="path",
                    target_type="track",
                    target_id=track.id,
                    current_value=track.path,
                    proposed_value=str(
                        library / f"Applied {track.id}{Path(track.path).suffix}"
                    ),
                ),
            )
        ),
    )
    for operation in session.scalars(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    ):
        operation.decision = "accepted"
    transition_bundle(session, write.bundle_id, BundleState.READY)
    apply_run = start_apply_run(session, write.bundle_id, idempotency_key="apply")
    session.commit()
    result = apply_review_run(session, apply_run.id, library_root=library)
    assert result.state == "applied"
    session.commit()
    return library, tracks, apply_run.id, original_titles


def test_review_undo_is_persistent_idempotent_and_restores_exact_tags(
    db_session: Session, tmp_path: Path
) -> None:
    library, tracks, apply_run_id, original_titles = _applied_run(db_session, tmp_path)
    track = tracks[0]
    source_run = next(
        changeset
        for changeset in db_session.scalars(select(ChangeSet))
        if changeset.source_ref.get("apply_run_id") == str(apply_run_id)
    )
    bundle_id = source_run.source_ref["review_bundle_id"]
    db_session.expire_all()

    first = enqueue_review_undo(
        db_session,
        int(bundle_id),
        apply_run_id=apply_run_id,
        idempotency_key="undo-once",
    )
    duplicate = enqueue_review_undo(
        db_session,
        int(bundle_id),
        apply_run_id=apply_run_id,
        idempotency_key="undo-once",
    )

    assert duplicate == first
    assert db_session.scalar(select(func.count()).select_from(ReviewUndoRun)) == 1
    assert db_session.scalar(
        select(func.count()).select_from(Job).where(Job.type == "undo_review_bundle")
    ) == 1
    undo_run = db_session.get(ReviewUndoRun, first.undo_run_id)
    assert undo_run is not None
    frozen_ids = [
        change_set_id
        for entry in undo_run.manifest["files"]
        for step in entry["steps"]
        for change_set_id in step["undo_change_set_ids"]
    ]
    frozen = list(
        db_session.scalars(select(ChangeSet).where(ChangeSet.id.in_(frozen_ids)))
    )
    assert len(frozen) == len(frozen_ids)
    assert all(change_set.created_by == "review_bundle_undo" for change_set in frozen)
    assert all(
        change_set.source_ref.get("review_undo_run_id") == str(first.undo_run_id)
        for change_set in frozen
    )
    result = apply_review_undo_run(db_session, first.undo_run_id, library_root=library)

    assert result.state == "undone"
    assert read_track(Path(track.path)).title == original_titles[track.id]
    persisted = db_session.get(ReviewUndoRun, first.undo_run_id)
    assert persisted is not None
    assert persisted.state == "undone"
    assert persisted.result is not None
    with pytest.raises(ReviewUndoError, match="already been undone"):
        enqueue_review_undo(
            db_session,
            int(bundle_id),
            apply_run_id=apply_run_id,
            idempotency_key="second-undo",
        )


def test_review_undo_fails_closed_when_file_changed_after_manifest_freeze(
    db_session: Session, tmp_path: Path
) -> None:
    library, tracks, apply_run_id, _original_titles = _applied_run(db_session, tmp_path)
    track = tracks[0]
    source_run = next(
        changeset
        for changeset in db_session.scalars(select(ChangeSet))
        if changeset.source_ref.get("apply_run_id") == str(apply_run_id)
    )
    bundle_id = int(source_run.source_ref["review_bundle_id"])
    enqueued = enqueue_review_undo(
        db_session,
        bundle_id,
        apply_run_id=apply_run_id,
        idempotency_key="frozen",
    )
    write_fields(Path(track.path), {"title": "External edit"})

    result = apply_review_undo_run(
        db_session, enqueued.undo_run_id, library_root=library
    )

    assert result.state == "failed"
    assert result.files[0].retryable is False
    assert read_track(Path(track.path)).title == "External edit"


def test_review_undo_cancel_and_retry_skip_an_already_restored_file(
    db_session: Session, tmp_path: Path
) -> None:
    library, tracks, apply_run_id, original_titles = _applied_run(
        db_session, tmp_path, ("silence.flac", "silence.mp3")
    )
    source_run = next(
        changeset
        for changeset in db_session.scalars(select(ChangeSet))
        if changeset.source_ref.get("apply_run_id") == str(apply_run_id)
    )
    bundle_id = int(source_run.source_ref["review_bundle_id"])
    enqueued = enqueue_review_undo(
        db_session,
        bundle_id,
        apply_run_id=apply_run_id,
        idempotency_key="cancel-between-files",
    )
    checkpoints = iter((False, True))

    first = apply_review_undo_run(
        db_session,
        enqueued.undo_run_id,
        library_root=library,
        should_cancel=lambda: next(checkpoints),
    )

    assert first.state == "partially_undone"
    assert first.cancelled is True
    assert [file.state for file in first.files] == ["undone", "pending"]
    restored_path = Path(tracks[0].path)
    restored_mtime = restored_path.stat().st_mtime_ns

    with Session(bind=db_session.get_bind()) as restarted_session:
        persisted = restarted_session.get(ReviewUndoRun, enqueued.undo_run_id)
        assert persisted is not None
        persisted_files = persisted.manifest["files"]
        assert [entry["state"] for entry in persisted_files] == ["undone", "pending"]
        assert persisted_files[0]["checkpoint"]["path"] == str(restored_path)
        retry = apply_review_undo_run(
            restarted_session, enqueued.undo_run_id, library_root=library
        )
        restored_tracks = {
            track.id: track.path
            for track in restarted_session.scalars(
                select(Track).where(Track.id.in_([track.id for track in tracks]))
            )
        }

    assert retry.state == "undone"
    assert restored_path.stat().st_mtime_ns == restored_mtime
    for track in tracks:
        assert read_track(Path(restored_tracks[track.id])).title == original_titles[track.id]


def test_review_undo_reconciles_a_crash_after_the_inverse_committed(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library, tracks, apply_run_id, original_titles = _applied_run(db_session, tmp_path)
    source_run = next(
        changeset
        for changeset in db_session.scalars(select(ChangeSet))
        if changeset.source_ref.get("apply_run_id") == str(apply_run_id)
    )
    enqueued = enqueue_review_undo(
        db_session,
        int(source_run.source_ref["review_bundle_id"]),
        apply_run_id=apply_run_id,
        idempotency_key="crash",
    )
    from muzilla.changes import bundle_undo

    original_apply = bundle_undo.apply_changeset

    def crash_after_commit(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        original_apply(*args, **kwargs)
        db_session.commit()
        raise KeyboardInterrupt("injected after inverse commit")

    monkeypatch.setattr(bundle_undo, "apply_changeset", crash_after_commit)
    with pytest.raises(KeyboardInterrupt, match="injected after inverse commit"):
        apply_review_undo_run(
            db_session, enqueued.undo_run_id, library_root=library
        )
    monkeypatch.setattr(bundle_undo, "apply_changeset", original_apply)

    retry = apply_review_undo_run(
        db_session, enqueued.undo_run_id, library_root=library
    )

    assert retry.state == "undone"
    assert read_track(Path(tracks[0].path)).title == original_titles[tracks[0].id]


def test_retry_inverse_stays_internal_if_worker_crashes_before_apply(
    db_session: Session,
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library, tracks, apply_run_id, _original_titles = _applied_run(
        db_session, tmp_path
    )
    track = tracks[0]
    source_run = next(
        changeset
        for changeset in db_session.scalars(select(ChangeSet))
        if changeset.source_ref.get("apply_run_id") == str(apply_run_id)
    )
    enqueued = enqueue_review_undo(
        db_session,
        int(source_run.source_ref["review_bundle_id"]),
        apply_run_id=apply_run_id,
        idempotency_key="retry-crash-before-apply",
    )
    from muzilla.changes import bundle_undo

    def fail_retryably(
        session: Session, change_set_id: int, **_kwargs: object
    ) -> ApplyResult:
        inverse = session.get(ChangeSet, change_set_id)
        assert inverse is not None
        inverse.state = "failed"
        inverse.error = "destination already exists"
        return ApplyResult(
            change_set_id,
            "failed",
            errors={track.id: "destination already exists"},
        )

    monkeypatch.setattr(bundle_undo, "apply_changeset", fail_retryably)
    failed = apply_review_undo_run(
        db_session, enqueued.undo_run_id, library_root=library
    )
    assert failed.state == "failed"
    assert failed.files[0].retryable is True

    def crash_before_apply(*_args: object, **_kwargs: object) -> ApplyResult:
        raise KeyboardInterrupt("injected before retry inverse apply")

    monkeypatch.setattr(bundle_undo, "apply_changeset", crash_before_apply)
    with pytest.raises(KeyboardInterrupt, match="before retry inverse apply"):
        apply_review_undo_run(
            db_session, enqueued.undo_run_id, library_root=library
        )

    queued_job = db_session.get(Job, enqueued.job_id)
    assert queued_job is not None
    queued_job.state = "failed"
    queued_job.error = "completed by failure-injection test"
    db_session.commit()

    with Session(bind=db_session.get_bind()) as restarted_session:
        undo_run = restarted_session.get(ReviewUndoRun, enqueued.undo_run_id)
        assert undo_run is not None
        inverse_ids = [
            change_set_id
            for entry in undo_run.manifest["files"]
            for step in entry["steps"]
            for change_set_id in step["undo_change_set_ids"]
        ]
        assert len(inverse_ids) == 2
        inverses = list(
            restarted_session.scalars(
                select(ChangeSet).where(ChangeSet.id.in_(inverse_ids))
            )
        )
        assert {inverse.id for inverse in inverses} == set(inverse_ids)
        assert all(
            inverse.created_by == "review_bundle_undo" for inverse in inverses
        )
        assert all(
            inverse.source_ref.get("review_undo_run_id")
            == str(enqueued.undo_run_id)
            for inverse in inverses
        )
        change_ids = {
            inverse.id: inverse.changes[0].id for inverse in inverses
        }

    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(library))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    from muzilla.api.app import create_app

    with TestClient(create_app()) as client:
        csrf = client.get("/api/auth/status").json()["csrf_token"]
        client.headers.update(
            {"Origin": "http://testserver", "X-CSRF-Token": csrf}
        )
        listed = client.get("/api/changesets")
        assert listed.status_code == 200
        listed_ids = {item["id"] for item in listed.json()["items"]}
        assert listed_ids.isdisjoint(inverse_ids)
        for inverse_id in inverse_ids:
            assert client.get(f"/api/changesets/{inverse_id}").status_code == 404
            assert (
                client.patch(
                    f"/api/changesets/{inverse_id}/changes",
                    json={
                        "decisions": [
                            {
                                "change_id": change_ids[inverse_id],
                                "decision": "rejected",
                            }
                        ]
                    },
                ).status_code
                == 404
            )
            assert (
                client.post(f"/api/changesets/{inverse_id}/apply").status_code
                == 404
            )
            assert (
                client.post(f"/api/changesets/{inverse_id}/undo").status_code
                == 404
            )


def test_review_undo_rejects_expired_source_journal(
    db_session: Session, tmp_path: Path
) -> None:
    _library, _tracks, apply_run_id, _original_titles = _applied_run(
        db_session, tmp_path
    )
    source_run = next(
        changeset
        for changeset in db_session.scalars(select(ChangeSet))
        if changeset.source_ref.get("apply_run_id") == str(apply_run_id)
    )
    source_run.state = "undo_expired"
    db_session.commit()

    with pytest.raises(ReviewUndoError, match="history expired"):
        enqueue_review_undo(
            db_session,
            int(source_run.source_ref["review_bundle_id"]),
            apply_run_id=apply_run_id,
            idempotency_key="expired",
        )
