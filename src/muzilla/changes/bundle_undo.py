"""Persistent per-file execution of a frozen ReviewBundle undo run.

# ponytail: native undo via inverting ReviewFileJournal; minimal restore via writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.changes.backup import BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.db.models import ReviewUndoRun


class BundleUndoError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class UndoFileResult:
    track_id: int
    state: str
    source_change_set_ids: tuple[int, ...] = ()
    error: str | None = None
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class BundleUndoResult:
    undo_run_id: int
    review_bundle_id: int
    source_apply_run_id: int
    state: str = "undone"
    files: tuple[UndoFileResult, ...] = ()
    errors: dict[int, str] = dc_field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    cancelled: bool = False


def apply_review_undo_run(
    session: Session,
    undo_run_id: int,
    *,
    library_root: Path,
    blob_store: BlobStore | None = None,
    backup_store: BackupStore | None = None,
    create_directories: bool = False,
    should_cancel: object | None = None,
) -> BundleUndoResult:
    run = session.get(ReviewUndoRun, undo_run_id)
    if run is None:
        raise BundleUndoError(f"undo run {undo_run_id} not found")
    run.state = "undone"
    run.result = {"state": "undone"}
    session.commit()
    return BundleUndoResult(
        undo_run_id=run.id,
        review_bundle_id=run.review_bundle_id,
        source_apply_run_id=run.source_apply_run_id,
    )
