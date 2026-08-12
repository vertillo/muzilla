"""Undo — a synthesized inverse ChangeSet run through the identical
apply path.

Not a special mechanism: undo builds a new DRAFT ChangeSet whose
Changes swap old_value/new_value from the applied changeset's Changes,
using the exact same `changes/builder.py` + `changes/applier.py` code
paths as everything else. This is what makes undo previewable as an
ordinary diff, partially acceptable, and self-composing (undo-of-undo
is redo) with zero special-cased code.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.db.models import ApplyJournal, Change, ChangeSet

_MISSING = object()
FROZEN_REVIEW_UNDO_CREATED_BY = "review_bundle_undo"


def mark_frozen_review_undo(
    change_set: ChangeSet, *, review_undo_run_id: int
) -> None:
    """Claim an inverse for one persistent ReviewBundle undo run."""
    change_set.created_by = FROZEN_REVIEW_UNDO_CREATED_BY
    change_set.source_ref = {
        **change_set.source_ref,
        "review_undo_run_id": str(review_undo_run_id),
    }


def _journaled_lyrics_before(
    session: Session, change_set_id: int, track_id: int
) -> object:
    journal = session.scalar(
        select(ApplyJournal)
        .where(
            ApplyJournal.change_set_id == change_set_id,
            ApplyJournal.track_id == track_id,
            ApplyJournal.phase == "tags",
            ApplyJournal.state == "done",
        )
        .order_by(ApplyJournal.id.desc())
    )
    if journal is None or "__muzilla_lyrics" not in journal.before_blob:
        return _MISSING
    text = journal.before_blob["__muzilla_lyrics"]
    if text is None:
        return None
    return {
        "text": str(text),
        "synced": bool(journal.before_blob.get("lyrics_synced", False)),
        "provider": "undo",
    }


def build_undo_changeset(session: Session, applied_change_set_id: int) -> ChangeSet:
    """Builds a new DRAFT ChangeSet that reverses every successfully
    applied Change in `applied_change_set_id`.

    Only Changes with `apply_state="applied"` are reversed — Changes
    that were rejected, left pending, or failed/conflicted never
    touched disk and have nothing to undo.
    """
    source_cs = session.get(ChangeSet, applied_change_set_id)
    if source_cs is None:
        raise ValueError(f"changeset {applied_change_set_id} not found")
    if source_cs.state not in ("applied", "partially_applied"):
        raise ValueError(
            f"changeset {applied_change_set_id} is in state {source_cs.state!r}; "
            "only applied or partially_applied changesets can be undone"
        )

    applied_changes = list(
        session.scalars(
            select(Change).where(
                Change.change_set_id == applied_change_set_id,
                Change.apply_state == "applied",
            ).order_by(Change.seq)
        )
    )
    if not applied_changes:
        raise ValueError(f"changeset {applied_change_set_id} has no applied changes to undo")

    edits: dict[int, list[FieldEdit]] = {}
    for c in applied_changes:
        if c.op == "embed_art":
            # Art has no JSON value — old_blob_id/new_blob_id carry the
            # reference, so the inverse swaps those instead of old/new_value.
            edit = FieldEdit(
                field=c.field,
                new_value=None,
                op="embed_art",
                is_manual=False,
                old_blob_id=c.new_blob_id,
                new_blob_id=c.old_blob_id,
            )
        elif c.op == "write_lyrics":
            before_lyrics = _journaled_lyrics_before(
                session, applied_change_set_id, c.entity_id
            )
            edit = FieldEdit(
                field=c.field,
                new_value=(
                    _from_json(c.old_value)
                    if before_lyrics is _MISSING
                    else before_lyrics
                ),
                op="write_lyrics",
                is_manual=False,
            )
        else:
            edit = FieldEdit(
                field=c.field,
                new_value=_from_json(c.old_value),
                op=_inverse_op(c.op),
                is_manual=False,
            )
        edits.setdefault(c.entity_id, []).append(edit)

    # Undo reverses whichever entity types were touched, so it must use
    # the same entity_type as the source changeset (track or group);
    # ChangeSets are homogeneous per changeset by construction.
    entity_type = applied_changes[0].entity_type

    undo_cs = build_changeset(
        session,
        title=f"Undo: {source_cs.title}",
        source=f"undo_of:{source_cs.id}",
        edits=edits,
        entity_type=entity_type,
        source_ref={"undo_of_id": str(source_cs.id)},
        scope_type=source_cs.scope_type,
        scope_id=source_cs.scope_id,
        created_by=source_cs.created_by,
        undo_of_id=source_cs.id,
    )
    # Undo changes are pre-accepted: the user already approved the
    # original change and is now explicitly asking to revert it, so a
    # second full review pass would be friction, not safety.
    for c in undo_cs.changes:
        c.decision = "accepted"
    undo_cs.stats["accepted"] = undo_cs.stats["total"]
    undo_cs.stats["pending"] = 0
    session.flush()
    return undo_cs


def _inverse_op(op: str) -> str:
    if op in ("clear", "strip"):
        return "set"
    if op == "set":
        return "set"
    return op


def _from_json(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value
