"""Temporary one-way bridge from ReviewBundle operations to the proven applier.

This is intentionally an adapter, not a second file-mutation engine.  A producer that
has moved to ReviewBundle can materialize its accepted current revision as one legacy
ChangeSet and delegate the actual write/journal/recovery behavior to ``apply_changeset``.
The bridge can be removed once the applier accepts Operations directly.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.applier import ApplyResult, apply_changeset
from muzilla.changes.backup import BackupStore
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.db.models import Operation, ProposalRevision, ReviewBundle
from muzilla.domain.reviews import BundleState, OperationKind


class ReviewAdapterError(ValueError):
    pass


class NoAcceptedReviewOperationsError(ReviewAdapterError):
    pass


class UnsupportedReviewOperation(ReviewAdapterError):
    """The temporary bridge deliberately refuses a kind it cannot preserve."""


def _blob_id(value: object | None, *, kind: OperationKind) -> int | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("blob_id"), int):
        raise UnsupportedReviewOperation(f"{kind.value} requires an art value with blob_id")
    blob_id = value["blob_id"]
    assert isinstance(blob_id, int)
    return blob_id


def field_edit_for_operation(operation: Operation) -> FieldEdit:
    kind = OperationKind(operation.kind)
    if kind is OperationKind.SET_TAG:
        return FieldEdit(field=operation.field, new_value=operation.proposed_value)
    if kind is OperationKind.WRITE_LYRICS:
        return FieldEdit(field=operation.field, new_value=operation.proposed_value, op="write_lyrics")
    if kind is OperationKind.EMBED_ART:
        return FieldEdit(
            field=operation.field,
            new_value=None,
            op="embed_art",
            new_blob_id=_blob_id(operation.proposed_value, kind=kind),
        )
    if kind is OperationKind.REMOVE_ART:
        return FieldEdit(field=operation.field, new_value=None, op="embed_art", new_blob_id=None)
    if kind is OperationKind.MOVE_FILE:
        if not isinstance(operation.proposed_value, str):
            raise UnsupportedReviewOperation("move_file requires a string proposed path")
        return FieldEdit(field=operation.field, new_value=operation.proposed_value, op="move")
    if kind is OperationKind.SET_REPLAY_GAIN:
        if not isinstance(operation.proposed_value, (int, float)) or isinstance(
            operation.proposed_value, bool
        ):
            raise UnsupportedReviewOperation("set_replay_gain requires a numeric proposed value")
        return FieldEdit(field=operation.field, new_value=operation.proposed_value)
    raise UnsupportedReviewOperation(
        "grouping_correction is not supported by the temporary legacy-applier adapter"
    )


def build_legacy_changeset_from_current_review(session: Session, review_bundle_id: int) -> int:
    """Materialize accepted operations from the current revision as one draft ChangeSet.

    The caller must dispatch the returned id through the existing ChangeSet job/applier;
    no file is touched here.  This is one-way and is intentionally not used by legacy
    producers, preventing a prolonged ReviewBundle/ChangeSet dual-write period.
    """
    bundle = session.get(ReviewBundle, review_bundle_id)
    if bundle is None:
        raise ReviewAdapterError(f"review bundle {review_bundle_id} not found")
    if BundleState(bundle.state) not in (BundleState.READY, BundleState.NEEDS_ATTENTION):
        raise ReviewAdapterError("review bundle must be ready before legacy apply adaptation")
    revision = session.scalar(
        select(ProposalRevision).where(
            ProposalRevision.review_bundle_id == bundle.id,
            ProposalRevision.is_current.is_(True),
        )
    )
    if revision is None:
        raise ReviewAdapterError("review bundle has no current revision")
    operations = list(
        session.scalars(
            select(Operation)
            .where(
                Operation.proposal_revision_id == revision.id,
                Operation.decision == "accepted",
            )
            .order_by(Operation.seq)
        )
    )
    if not operations:
        raise NoAcceptedReviewOperationsError("review has no accepted operations")
    if {operation.target_type for operation in operations} != {"track"}:
        raise UnsupportedReviewOperation("temporary legacy-applier adapter supports track operations only")

    edits: dict[int, list[FieldEdit]] = {}
    for operation in operations:
        edits.setdefault(operation.target_id, []).append(field_edit_for_operation(operation))
    changeset = build_changeset(
        session,
        title=f"Apply review {bundle.title}",
        source="manual_edit",
        edits=edits,
        source_ref={"review_bundle_id": str(bundle.id), "proposal_revision_id": str(revision.id)},
        scope_type=bundle.scope_type,
        scope_id=bundle.scope_id,
        created_by="review_adapter",
        candidate_source=revision.candidate_source,
        candidate_ref=revision.candidate_ref,
    )
    for change in changeset.changes:
        change.decision = "accepted"
    changeset.stats = {
        "total": len(changeset.changes),
        "accepted": len(changeset.changes),
        "rejected": 0,
        "pending": 0,
    }
    session.flush()
    return changeset.id


def apply_current_review_via_legacy_applier(
    session: Session,
    review_bundle_id: int,
    *,
    library_root: Path | None = None,
    create_directories: bool = False,
    blob_store: BlobStore | None = None,
    backup_store: BackupStore | None = None,
) -> ApplyResult:
    """Invoke the existing, journaled applier for an accepted current revision."""
    changeset_id = build_legacy_changeset_from_current_review(session, review_bundle_id)
    return apply_changeset(
        session,
        changeset_id,
        library_root=library_root,
        create_directories=create_directories,
        blob_store=blob_store,
        backup_store=backup_store,
    )
