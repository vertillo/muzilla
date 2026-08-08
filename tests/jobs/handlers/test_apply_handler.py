from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.config.schema import Config
from muzilla.db.models import ApplyRun, Operation, OperationAttempt, Track
from muzilla.domain.reviews import BundleState
from muzilla.jobs.handlers.apply import (
    handle_apply_changeset,
    handle_apply_review_bundle,
    handle_undo_changeset,
)
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.scan import scan_library
from muzilla.providers.set import ProviderSet
from muzilla.services.reviews import (
    OperationDraft,
    put_revision,
    start_apply_run,
    transition_bundle,
)
from muzilla.tags.reader import read_track

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "audio"


def _context(config: Config | None = None) -> WorkerContext:
    return WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=config or Config(),
    )


def _scan_one(db_session: Session, tmp_path: Path) -> Track:
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "silence.mp3", library / "silence.mp3")
    scan_library(db_session, library)
    db_session.commit()
    return db_session.query(Track).filter(Track.filename == "silence.mp3").one()


async def test_handle_apply_changeset_tag_edit(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = build_changeset(
        db_session,
        title="Edit",
        source="manual_edit",
        edits={track.id: [FieldEdit(field="title", new_value="New Title", is_manual=True)]},
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    job = enqueue(db_session, type="apply_changeset", payload={"change_set_id": cs.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_apply_changeset(db_session, job, progress, _context())

    assert result["state"] == "applied"
    db_session.refresh(track)
    assert track.title == "New Title"


async def test_handle_apply_review_bundle_reports_per_file_result(
    db_session: Session, tmp_path: Path
) -> None:
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    write = put_revision(
        db_session,
        logical_key=f"track:{track.id}",
        title="Review apply handler",
        scope_type="track",
        scope_id=track.id,
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
            ]
        },
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value=track.title,
                proposed_value="Bundle title",
            ),
        ),
    )
    operation = db_session.query(Operation).one()
    operation.decision = "accepted"
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    run = start_apply_run(db_session, write.bundle_id, idempotency_key="handler")
    db_session.commit()
    config = Config(
        storage={"library_root": library, "blob_dir": tmp_path / "blobs"}
    )
    job = enqueue(db_session, type="apply_review_bundle", payload={"apply_run_id": run.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_apply_review_bundle(db_session, job, progress, _context(config))

    assert result["state"] == "applied"
    assert result["atomicity"] == "per_file"
    assert result["files"] == [
        {
            "track_id": track.id,
            "state": "applied",
            "applied_operation_ids": [operation.id],
            "error": None,
        }
    ]


async def test_handle_apply_review_bundle_stops_at_next_file_after_cancel(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    for name in ("silence.flac", "silence.mp3"):
        shutil.copy(FIXTURES / name, library / name)
    scan_library(db_session, library)
    db_session.commit()
    tracks = list(db_session.scalars(select(Track).order_by(Track.id)))
    write = put_revision(
        db_session,
        logical_key="group:cancel-apply",
        title="Cancel apply between files",
        scope_type="group",
        scope_id=None,
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
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value=track.title,
                proposed_value=f"Applied {track.id}",
            )
            for track in tracks
        ),
    )
    for operation in db_session.scalars(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    ):
        operation.decision = "accepted"
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    run = start_apply_run(db_session, write.bundle_id, idempotency_key="cancel-handler")
    db_session.commit()
    config = Config(storage={"library_root": library, "blob_dir": tmp_path / "blobs"})
    job = enqueue(db_session, type="apply_review_bundle", payload={"apply_run_id": run.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    from muzilla.changes import bundle_applier

    original_apply = bundle_applier.apply_changeset
    calls = 0

    def cancel_after_first(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal calls
        result = original_apply(*args, **kwargs)
        calls += 1
        if calls == 1:
            job.cancel_requested = True
            job.state = "cancelling"
            db_session.commit()
        return result

    monkeypatch.setattr(bundle_applier, "apply_changeset", cancel_after_first)

    with pytest.raises(JobCancelled) as cancelled:
        await handle_apply_review_bundle(db_session, job, progress, _context(config))

    assert calls == 1
    assert cancelled.value.result is not None
    assert cancelled.value.result["partial"] is True
    refreshed_run = db_session.get(ApplyRun, run.id)
    assert refreshed_run is not None
    assert refreshed_run.state == "partially_applied"
    attempts = list(
        db_session.scalars(
            select(OperationAttempt)
            .where(OperationAttempt.apply_run_id == run.id)
            .order_by(OperationAttempt.id)
        )
    )
    assert [attempt.state for attempt in attempts] == ["applied", "pending"]
    assert read_track(Path(tracks[0].path)).title == f"Applied {tracks[0].id}"
    assert read_track(Path(tracks[1].path)).title != f"Applied {tracks[1].id}"


async def test_handle_apply_changeset_move_uses_context_config(
    db_session: Session, tmp_path: Path
) -> None:
    """Confirms the job handler actually threads
    context.config.storage.library_root /
    context.config.paths.create_directories into apply_changeset — a
    move Change with no library_root guardrail would succeed even
    writing outside the intended library, so this specifically proves
    the config values reach applier.py, not just that a move works."""
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    new_path = str(library / "SubDir" / "renamed.mp3")

    cs = build_changeset(
        db_session,
        title="Rename",
        source="rename",
        edits={track.id: [FieldEdit(field="path", new_value=new_path, op="move")]},
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    config = Config(
        storage={"library_root": library},
        paths={"create_directories": True},
    )
    job = enqueue(db_session, type="apply_changeset", payload={"change_set_id": cs.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_apply_changeset(db_session, job, progress, _context(config))

    assert result["state"] == "applied"
    assert Path(new_path).exists()
    db_session.refresh(track)
    assert track.path == new_path


async def test_handle_apply_changeset_backup_via_payload(
    db_session: Session, tmp_path: Path
) -> None:
    """Proves payload["backup"]=True reaches applier.py's backup_store,
    not just that a backup-less apply works — mirrors the move test's
    reasoning above (config threading is meaningless if unverified)."""
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"
    original_bytes = (library / "silence.mp3").read_bytes()

    cs = build_changeset(
        db_session,
        title="Edit",
        source="manual_edit",
        edits={track.id: [FieldEdit(field="title", new_value="New Title", is_manual=True)]},
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    config = Config(storage={"library_root": library, "backup_dir": tmp_path / "backups"})
    job = enqueue(
        db_session, type="apply_changeset", payload={"change_set_id": cs.id, "backup": True}
    )
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_apply_changeset(db_session, job, progress, _context(config))

    assert result["state"] == "applied"
    backup_path = tmp_path / "backups" / "silence.mp3"
    assert backup_path.exists()
    assert backup_path.read_bytes() == original_bytes


async def test_handle_apply_changeset_backup_defaults_from_config(
    db_session: Session, tmp_path: Path
) -> None:
    """No "backup" key in the payload at all -> falls back to
    config.apply.backup, not to False unconditionally."""
    track = _scan_one(db_session, tmp_path)
    library = tmp_path / "library"

    cs = build_changeset(
        db_session,
        title="Edit",
        source="manual_edit",
        edits={track.id: [FieldEdit(field="title", new_value="New Title", is_manual=True)]},
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    config = Config(
        storage={"library_root": library, "backup_dir": tmp_path / "backups"},
        apply={"backup": True},
    )
    job = enqueue(db_session, type="apply_changeset", payload={"change_set_id": cs.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    result = await handle_apply_changeset(db_session, job, progress, _context(config))

    assert result["state"] == "applied"
    assert (tmp_path / "backups" / "silence.mp3").exists()


async def test_handle_apply_changeset_binds_change_set_id_to_log_context(
    db_session: Session, tmp_path: Path
) -> None:
    """docs/PLAN.md §11d: apply/undo bind change_set_id via
    change_set_context around the call into applier.py."""
    from muzilla.logging import _change_set_id_var

    track = _scan_one(db_session, tmp_path)
    cs = build_changeset(
        db_session,
        title="Edit",
        source="manual_edit",
        edits={track.id: [FieldEdit(field="title", new_value="New Title", is_manual=True)]},
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    job = enqueue(db_session, type="apply_changeset", payload={"change_set_id": cs.id})
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    assert _change_set_id_var.get() is None
    await handle_apply_changeset(db_session, job, progress, _context())
    assert _change_set_id_var.get() is None  # reset after the handler returns


async def test_handle_undo_changeset(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    original_title = track.title
    cs = build_changeset(
        db_session,
        title="Edit",
        source="manual_edit",
        edits={track.id: [FieldEdit(field="title", new_value="Changed")]},
    )
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    apply_job = enqueue(db_session, type="apply_changeset", payload={"change_set_id": cs.id})
    apply_progress = ProgressReporter(db_session, apply_job.id, coalesce_ms=0)
    await handle_apply_changeset(db_session, apply_job, apply_progress, _context())
    db_session.commit()

    undo_job = enqueue(db_session, type="undo_changeset", payload={"change_set_id": cs.id})
    undo_progress = ProgressReporter(db_session, undo_job.id, coalesce_ms=0)
    undo_result = await handle_undo_changeset(db_session, undo_job, undo_progress, _context())

    assert "undo_change_set_id" in undo_result
    undo_apply_job = enqueue(
        db_session, type="apply_changeset", payload={"change_set_id": undo_result["undo_change_set_id"]}
    )
    undo_apply_progress = ProgressReporter(db_session, undo_apply_job.id, coalesce_ms=0)
    await handle_apply_changeset(db_session, undo_apply_job, undo_apply_progress, _context())
    db_session.commit()

    db_session.refresh(track)
    assert track.title == original_title
