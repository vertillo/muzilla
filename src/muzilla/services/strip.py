"""Strip-rules service: wires domain.fields.default_strip_fields() into
a `strip_tags` ChangeSet.

`default_strip_fields()` already exists in domain/fields.py (comment,
encoder) — this module is the "propose clearing them" plumbing.
Per-field decisions still default per changes/builder.py's
`default_decision_for_kind`: fields marked `default_strip=True` in the
registry auto-accept, matching "configurable per change kind."

`strip_field_names` (optional) lets a caller override which fields
count as strippable — services/settings.py's stored strip_fields setting is
the real caller of this override;
None keeps the original behavior (the registry's built-in set) for
every other caller (CLI, existing tests).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.db.models import ChangeSet, Track
from muzilla.domain import fields as field_registry


def propose_strip(
    session: Session,
    *,
    track_ids: list[int],
    created_by: str = "web",
    strip_field_names: list[str] | None = None,
) -> ChangeSet:
    """Builds a DRAFT `strip_tags` ChangeSet clearing every
    `default_strip` field currently populated on the given tracks.
    Fields already empty are skipped — clearing an empty field is a
    no-op and would only add review noise.
    """
    if not track_ids:
        raise ValueError("no tracks selected")

    if strip_field_names is None:
        strip_field_names = [f.name for f in field_registry.default_strip_fields()]
    tracks = list(session.scalars(select(Track).where(Track.id.in_(track_ids))))

    edits: dict[int, list[FieldEdit]] = {}
    for track in tracks:
        track_edits = []
        for field_name in strip_field_names:
            current = getattr(track, field_name, None)
            if current not in (None, "", (), []):
                track_edits.append(FieldEdit(field=field_name, new_value=None, op="strip"))
        if track_edits:
            edits[track.id] = track_edits

    if not edits:
        raise ValueError("no strippable tags present on the selected tracks")

    return build_changeset(
        session,
        title=f"Strip tags: {len(edits)} tracks",
        source="strip_tags",
        edits=edits,
        entity_type="track",
        scope_type="track",
        created_by=created_by,
    )
