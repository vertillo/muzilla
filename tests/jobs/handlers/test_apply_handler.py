from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.config.schema import Config
from muzilla.db.models import Track
from muzilla.jobs.handlers.apply import handle_apply_changeset, handle_undo_changeset
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.pipeline.scan import scan_library
from muzilla.providers.set import ProviderSet

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
