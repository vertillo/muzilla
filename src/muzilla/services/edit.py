"""Manual tag editing service — single-track and bulk.

Every manual edit produces a DRAFT ChangeSet via changes/builder.py and
lands in the same diff review before touching disk; there is no "quick
save" bypass. Bulk find-and-replace lives here too, since it is still just
manual editing at scale, not a distinct mutation path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.db.models import ChangeSet, Track
from muzilla.domain import fields as field_registry


class EditValidationError(ValueError):
    pass


def _validate_field(field_name: str) -> None:
    if field_name not in field_registry.FIELDS:
        raise EditValidationError(f"unknown field: {field_name!r}")
    fdef = field_registry.get(field_name)
    if not fdef.editable:
        raise EditValidationError(f"field {field_name!r} is not editable")


def edit_track(
    session: Session,
    *,
    track_id: int,
    field_values: dict[str, object],
    created_by: str = "web",
) -> ChangeSet:
    """Single-track manual edit: one DRAFT ChangeSet covering every
    field in `field_values` for the one track."""
    for f in field_values:
        _validate_field(f)

    edits = {
        track_id: [
            FieldEdit(field=f, new_value=v, is_manual=True) for f, v in field_values.items()
        ]
    }
    return build_changeset(
        session,
        title=f"Manual edit: track {track_id}",
        source="manual_edit",
        edits=edits,
        entity_type="track",
        scope_type="track",
        scope_id=track_id,
        created_by=created_by,
    )


@dataclass(frozen=True, slots=True)
class BulkEditField:
    field: str
    new_value: object
    """Only fields explicitly included here are edited — the caller
    (API/CLI) is responsible for omitting fields the user left at
    '<multiple values>' so a bulk editor never silently flattens
    distinct values across the selection."""


def edit_tracks_bulk(
    session: Session,
    *,
    track_ids: list[int],
    field_values: list[BulkEditField],
    created_by: str = "web",
) -> ChangeSet:
    """Bulk edit: one ChangeSet spanning N tracks, each field applied
    identically across the whole selection."""
    for bf in field_values:
        _validate_field(bf.field)
    if not track_ids:
        raise EditValidationError("no tracks selected")

    edits = {
        track_id: [
            FieldEdit(field=bf.field, new_value=bf.new_value, is_manual=True)
            for bf in field_values
        ]
        for track_id in track_ids
    }
    return build_changeset(
        session,
        title=f"Bulk edit: {len(track_ids)} tracks",
        source="manual_edit",
        edits=edits,
        entity_type="track",
        scope_type="track",
        created_by=created_by,
    )


@dataclass(frozen=True, slots=True)
class FindReplacePreviewRow:
    track_id: int
    old_value: str
    new_value: str


def preview_find_replace(
    session: Session,
    *,
    track_ids: list[int],
    field: str,
    find: str,
    replace: str,
    use_regex: bool = False,
) -> list[FindReplacePreviewRow]:
    """Live preview of a find-and-replace across a selection, without
    staging anything — the "practical fix for era-specific tagging
    damage, e.g. every 2008-era
    file having a boilerplate `Comment: Ripped by...`.
    """
    _validate_field(field)
    pattern = re.compile(find) if use_regex else re.compile(re.escape(find))

    rows: list[FindReplacePreviewRow] = []
    tracks: list[Track] = (
        list(session.scalars(select(Track).where(Track.id.in_(track_ids)))) if track_ids else []
    )
    for track in tracks:
        old_value = getattr(track, field, None)
        if not isinstance(old_value, str) or not old_value:
            continue
        new_value = pattern.sub(replace, old_value)
        if new_value != old_value:
            rows.append(FindReplacePreviewRow(track_id=track.id, old_value=old_value, new_value=new_value))
    return rows


def apply_find_replace(
    session: Session,
    *,
    track_ids: list[int],
    field: str,
    find: str,
    replace: str,
    use_regex: bool = False,
    created_by: str = "web",
) -> ChangeSet:
    """Stages a DRAFT ChangeSet from a find-and-replace preview — still
    just manual editing, still reviewed before anything touches disk."""
    preview = preview_find_replace(
        session, track_ids=track_ids, field=field, find=find, replace=replace, use_regex=use_regex
    )
    if not preview:
        raise EditValidationError("find/replace matched no values in the selection")

    edits = {
        row.track_id: [FieldEdit(field=field, new_value=row.new_value, is_manual=True)]
        for row in preview
    }
    return build_changeset(
        session,
        title=f"Find & replace: {field}",
        source="manual_edit",
        edits=edits,
        entity_type="track",
        scope_type="track",
        source_ref={"find": find, "replace": replace, "regex": str(use_regex)},
        created_by=created_by,
    )
