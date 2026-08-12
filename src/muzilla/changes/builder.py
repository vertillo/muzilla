"""Builds DRAFT ChangeSets from field edits (docs/product-spec.md).

The single construction path shared by manual editing (single + bulk),
strip-rules, and grouping corrections — "every mutation becomes rows in
`changes` first" (CLAUDE.md). Nothing here touches disk; it only stages
rows. `changes/applier.py` is the only place that writes files, and it
always operates on a DRAFT ChangeSet built here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from muzilla.db.models import Change, ChangeSet, Track, TrackGroup
from muzilla.domain import fields as field_registry


@dataclass(frozen=True, slots=True)
class FieldEdit:
    field: str
    new_value: Any
    op: str = "set"
    """set | clear | strip | append | move | embed_art | write_lyrics"""
    confidence: float | None = None
    is_manual: bool = False
    old_blob_id: int | None = None
    new_blob_id: int | None = None
    """Only meaningful for op='embed_art': the Blob row (already stored
    via changes/blobstore.py before staging) this Change points at.
    `new_value`/`old_value` stay None for embed_art — art is a binary
    pseudo-field with no JSON tag payload (docs/product-spec.md diff
    model), so the blob id columns carry the reference instead."""


def build_changeset(
    session: Session,
    *,
    title: str,
    source: str,
    edits: dict[int, list[FieldEdit]],
    entity_type: str = "track",
    source_ref: dict[str, str] | None = None,
    scope_type: str = "track",
    scope_id: int | None = None,
    created_by: str = "web",
    candidate_source: str | None = None,
    candidate_ref: str | None = None,
    undo_of_id: int | None = None,
) -> ChangeSet:
    """Build (and add to `session`, but not commit) a DRAFT ChangeSet.

    `edits` maps entity_id -> list of FieldEdit for that entity. Every
    change kind in Phase 2 (manual_edit, strip_tags, grouping_correction,
    undo_of:<id>) goes through this one function — see docs/product-spec.md's
    "there is exactly one path to disk."
    """
    if source not in (
        "manual_edit",
        "match_proposal",
        "rename",
        "strip_tags",
        "grouping_correction",
        "enrichment",
    ) and not source.startswith("undo_of:"):
        raise ValueError(f"unrecognized changeset source: {source!r}")

    change_set = ChangeSet(
        title=title,
        source=source,
        source_ref=source_ref or {},
        state="draft",
        scope_type=scope_type,
        scope_id=scope_id,
        created_by=created_by,
        candidate_source=candidate_source,
        candidate_ref=candidate_ref,
        undo_of_id=undo_of_id,
        stats={},
    )
    session.add(change_set)
    session.flush()  # assign change_set.id for FK use below

    seq = 0
    for entity_id, entity_edits in edits.items():
        entity: Track | TrackGroup
        if entity_type == "track":
            track = session.get(Track, entity_id)
            if track is None:
                raise ValueError(f"track {entity_id} not found")
            entity = track
        elif entity_type == "group":
            group = session.get(TrackGroup, entity_id)
            if group is None:
                raise ValueError(f"group {entity_id} not found")
            entity = group
        else:
            raise ValueError(f"unrecognized entity_type: {entity_type!r}")

        for edit in entity_edits:
            if edit.op == "embed_art":
                # "art" is a binary pseudo-field (differ.py special-cases
                # it, same as it has no domain.fields entry) — its
                # "current value" for severity purposes is whatever blob
                # id the entity already points at, not a JSON tag value.
                old_blob_id = edit.old_blob_id
                if old_blob_id is None:
                    old_blob_id = getattr(entity, "art_blob_id", None)
                severity = "normal"
                decision = default_decision_for_kind(source, edit.field)
                change = Change(
                    seq=seq,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    field=edit.field,
                    op=edit.op,
                    old_value=None,
                    new_value=None,
                    old_blob_id=old_blob_id,
                    new_blob_id=edit.new_blob_id,
                    confidence=edit.confidence,
                    severity=severity,
                    decision=decision,
                    apply_state="pending",
                    is_manual=edit.is_manual,
                )
                change_set.changes.append(change)
                seq += 1
                continue

            if edit.op == "write_lyrics":
                # Lyrics text isn't cached on the entity (only the
                # has_lyrics/lyrics_synced presence flags are — same
                # reasoning as art), so severity's "was something there
                # before" check reads the presence flag instead of a
                # nonexistent `entity.lyrics` attribute.
                had_lyrics = bool(getattr(entity, "has_lyrics", False))
                severity = "destructive" if had_lyrics and edit.new_value is None else "normal"
                decision = default_decision_for_kind(source, edit.field)
                change = Change(
                    seq=seq,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    field=edit.field,
                    op=edit.op,
                    old_value=None,
                    new_value=edit.new_value,
                    confidence=edit.confidence,
                    severity=severity,
                    decision=decision,
                    apply_state="pending",
                    is_manual=edit.is_manual,
                )
                change_set.changes.append(change)
                seq += 1
                continue

            old_value = getattr(entity, edit.field, None)
            severity = _severity_for(edit.field, old_value, edit.new_value, edit.op)
            decision = default_decision_for_kind(source, edit.field)
            change = Change(
                seq=seq,
                entity_type=entity_type,
                entity_id=entity_id,
                field=edit.field,
                op=edit.op,
                old_value=_to_jsonable(old_value),
                new_value=_to_jsonable(edit.new_value),
                confidence=edit.confidence,
                severity=severity,
                decision=decision,
                apply_state="pending",
                is_manual=edit.is_manual,
            )
            # Appending through the relationship (rather than setting
            # change_set_id directly) keeps change_set.changes populated
            # in-session — with expire_on_commit=False, a bare FK write
            # would leave the in-memory collection stale after commit.
            change_set.changes.append(change)
            seq += 1

    stats = _compute_stats(change_set, session)
    change_set.stats = stats
    session.flush()
    return change_set


def _compute_stats(change_set: ChangeSet, session: Session) -> dict[str, int]:
    counts = {"total": 0, "accepted": 0, "rejected": 0, "pending": 0}
    for change in change_set.changes:
        counts["total"] += 1
        counts[change.decision] = counts.get(change.decision, 0) + 1
    return counts


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


def _severity_for(field_name: str, old_value: Any, new_value: Any, op: str) -> str:
    if op == "move":
        return "destructive"
    if op in ("clear", "strip"):
        return "destructive" if old_value not in (None, "", (), []) else "normal"
    if new_value in (None, "", (), []) and old_value not in (None, "", (), []):
        return "destructive"
    return "normal"


def default_decision_for_kind(source: str, field_name: str) -> str:
    """Defaults are configurable per change *kind* (docs/product-spec.md):
    strip ops on always-strip fields auto-accept; everything else
    (including grouping corrections, which the user explicitly
    requested) starts pending except where noted below."""
    if source == "strip_tags":
        strip_names = {f.name for f in field_registry.default_strip_fields()}
        if field_name in strip_names:
            return "accepted"
    if source == "grouping_correction":
        # The user explicitly requested this action (merge/split/pin);
        # auto-accepting is the correct default so confirming a grouping
        # action doesn't require a second review pass.
        return "accepted"
    if source == "enrichment":
        # ReplayGain/lyrics/art-fill are non-destructive additions to
        # fields the user wasn't actively using (docs/product-spec.md:
        # "complete, not just correct" metadata) — auto-accept so a
        # bulk enrichment job doesn't dump thousands of pending rows
        # into every album's review queue. Undo remains available like
        # any other applied ChangeSet if a result is unwanted.
        return "accepted"
    return "pending"
