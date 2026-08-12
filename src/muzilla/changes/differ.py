"""Field-level diff computation.

Produces a `FieldDiff` describing how one field changed, shaped so the
review UI can render char-level inline highlights, ordered-set deltas
for multi-valued fields, and binary-field metadata without ever putting
raw image bytes in the diff payload. This module is pure/network-free:
it takes old/new values and domain.fields metadata and returns data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal

from muzilla.domain import fields as field_registry
from muzilla.domain.fields import FieldType

DiffKind = Literal["text", "multi_text", "binary", "scalar"]


@dataclass(frozen=True, slots=True)
class InlineSpan:
    """One opcode-derived span of an inline text diff.

    `op` is one of "equal" | "insert" | "delete" — SequenceMatcher's
    "replace" opcode is split into a delete span (old) + insert span
    (new) so old/new each get a flat list of same/changed spans.
    """

    op: Literal["equal", "insert", "delete"]
    text: str


@dataclass(frozen=True, slots=True)
class MultiValueDiff:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: tuple[str, ...]
    """Preserves order as it will appear in `new_value`."""


@dataclass(frozen=True, slots=True)
class BinaryDiff:
    old_summary: str | None
    """e.g. '1200x1200 image/jpeg 84.2KB', or None if there was no art."""
    new_summary: str | None
    old_blob_id: int | None
    new_blob_id: int | None


@dataclass(frozen=True, slots=True)
class FieldDiff:
    field: str
    label: str
    kind: DiffKind
    old_value: Any
    new_value: Any
    severity: Literal["normal", "destructive"]
    old_spans: tuple[InlineSpan, ...] = ()
    new_spans: tuple[InlineSpan, ...] = ()
    multi: MultiValueDiff | None = None
    binary: BinaryDiff | None = None


def _inline_diff(old: str, new: str) -> tuple[tuple[InlineSpan, ...], tuple[InlineSpan, ...]]:
    """Char-level inline diff via difflib.SequenceMatcher opcodes.

    "Beatles" -> "The Beatles" must highlight only the changed head,
    not re-render the whole string as replaced — that distinction is
    the difference between a usable and an infuriating review UI.
    """
    matcher = SequenceMatcher(a=old, b=new, autojunk=False)
    old_spans: list[InlineSpan] = []
    new_spans: list[InlineSpan] = []
    for opcode, a1, a2, b1, b2 in matcher.get_opcodes():
        if opcode == "equal":
            old_spans.append(InlineSpan("equal", old[a1:a2]))
            new_spans.append(InlineSpan("equal", new[b1:b2]))
        elif opcode == "delete":
            old_spans.append(InlineSpan("delete", old[a1:a2]))
        elif opcode == "insert":
            new_spans.append(InlineSpan("insert", new[b1:b2]))
        elif opcode == "replace":
            old_spans.append(InlineSpan("delete", old[a1:a2]))
            new_spans.append(InlineSpan("insert", new[b1:b2]))
    return tuple(old_spans), tuple(new_spans)


def _multi_value_diff(old: tuple[str, ...], new: tuple[str, ...]) -> MultiValueDiff:
    """Ordered-set diff for multi-valued fields (artists, genres): they
    diff as added/removed/reordered, never as joined strings."""
    old_set = set(old)
    new_set = set(new)
    added = tuple(v for v in new if v not in old_set)
    removed = tuple(v for v in old if v not in new_set)
    unchanged = tuple(v for v in new if v in old_set)
    return MultiValueDiff(added=added, removed=removed, unchanged=unchanged)


def _is_destructive(field_name: str, old: Any, new: Any, op: str) -> bool:
    """Clearing a populated field, or a move, is destructive — the UI
    requires an explicit toggle to bulk-accept those."""
    if op == "move":
        return True
    if op in ("clear", "strip"):
        return bool(old)  # clearing an already-empty field is a no-op, not destructive
    return new in (None, "", ()) and old not in (None, "", ())


def diff_field(
    field_name: str,
    old_value: Any,
    new_value: Any,
    *,
    op: str = "set",
    old_blob_id: int | None = None,
    new_blob_id: int | None = None,
    old_binary_summary: str | None = None,
    new_binary_summary: str | None = None,
) -> FieldDiff:
    """Compute a FieldDiff for one field. `field_name` must be a name
    registered in domain.fields, OR 'art' for the binary art pseudo-field
    (not in the registry since it has no scalar tag mapping)."""
    severity: Literal["normal", "destructive"] = (
        "destructive" if _is_destructive(field_name, old_value, new_value, op) else "normal"
    )

    if field_name == "art" or op in ("embed_art",):
        return FieldDiff(
            field=field_name,
            label="Album Art",
            kind="binary",
            old_value=None,
            new_value=None,
            severity=severity,
            binary=BinaryDiff(
                old_summary=old_binary_summary,
                new_summary=new_binary_summary,
                old_blob_id=old_blob_id,
                new_blob_id=new_blob_id,
            ),
        )

    if field_name == "lyrics" or op == "write_lyrics":
        # Payload is {"text": str, "synced": bool} | None (changes/
        # builder.py) — unwrap to plain text for the same char-level
        # inline diff other text fields get, rather than dumping a raw
        # dict into the generic scalar fallback below.
        old_text = old_value.get("text") if isinstance(old_value, dict) else None
        new_text = new_value.get("text") if isinstance(new_value, dict) else None
        old_spans, new_spans = _inline_diff(old_text or "", new_text or "")
        return FieldDiff(
            field=field_name,
            label="Lyrics",
            kind="text",
            old_value=old_text,
            new_value=new_text,
            severity=severity,
            old_spans=old_spans,
            new_spans=new_spans,
        )

    fdef = field_registry.FIELDS.get(field_name)
    if fdef is None:
        # Grouping-correction pseudo-fields (track_ids_add/_remove,
        # is_pinned, and other TrackGroup-only columns) live outside
        # domain.fields — it is the registry for track tag fields, not
        # group metadata. Render as a generic scalar diff rather than
        # raising, so the review UI can still show *something* readable
        # for a group-scoped Change.
        return FieldDiff(
            field=field_name,
            label=field_name.replace("_", " ").title(),
            kind="scalar",
            old_value=old_value,
            new_value=new_value,
            severity=severity,
        )

    if fdef.type == FieldType.MULTI_TEXT:
        old_tuple = tuple(old_value or ())
        new_tuple = tuple(new_value or ())
        return FieldDiff(
            field=field_name,
            label=fdef.label,
            kind="multi_text",
            old_value=old_tuple,
            new_value=new_tuple,
            severity=severity,
            multi=_multi_value_diff(old_tuple, new_tuple),
        )

    if fdef.type == FieldType.TEXT and isinstance(old_value, str | type(None)) and isinstance(
        new_value, str | type(None)
    ):
        old_str = old_value or ""
        new_str = new_value or ""
        old_spans, new_spans = _inline_diff(old_str, new_str)
        return FieldDiff(
            field=field_name,
            label=fdef.label,
            kind="text",
            old_value=old_value,
            new_value=new_value,
            severity=severity,
            old_spans=old_spans,
            new_spans=new_spans,
        )

    return FieldDiff(
        field=field_name,
        label=fdef.label,
        kind="scalar",
        old_value=old_value,
        new_value=new_value,
        severity=severity,
    )


@dataclass(frozen=True, slots=True)
class EntityDiff:
    """All FieldDiffs for one entity (track or group) within a ChangeSet."""

    entity_type: str
    entity_id: int
    field_diffs: tuple[FieldDiff, ...] = field(default_factory=tuple)
