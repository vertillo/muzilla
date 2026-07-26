"""Pydantic schemas for /api/changesets, /api/tracks/{id} PATCH.

Mirrors services.changesets / services.changes.differ field-for-field
(kept as separate schemas rather than reusing the dataclasses so the
wire shape can evolve independently — same convention as
api/schemas/tracks.py).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class InlineSpanOut(BaseModel):
    op: Literal["equal", "insert", "delete"]
    text: str


class MultiValueDiffOut(BaseModel):
    added: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: tuple[str, ...]


class BinaryDiffOut(BaseModel):
    old_summary: str | None
    new_summary: str | None
    old_blob_id: int | None
    new_blob_id: int | None


class FieldDiffOut(BaseModel):
    field: str
    label: str
    kind: Literal["text", "multi_text", "binary", "scalar"]
    old_value: object
    new_value: object
    severity: Literal["normal", "destructive"]
    old_spans: tuple[InlineSpanOut, ...] = ()
    new_spans: tuple[InlineSpanOut, ...] = ()
    multi: MultiValueDiffOut | None = None
    binary: BinaryDiffOut | None = None


class ChangeOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    seq: int
    entity_type: str
    entity_id: int
    field: str
    op: str
    old_value: object
    new_value: object
    confidence: float | None
    severity: str
    decision: str
    apply_state: str
    is_manual: bool
    diff: FieldDiffOut


class ChangeSetSummaryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str
    source: str
    state: str
    scope_type: str
    scope_id: int | None
    created_by: str
    candidate_source: str | None
    candidate_ref: str | None
    undo_of_id: int | None
    stats: dict[str, int]
    error: str | None


class ChangeSetDetailOut(ChangeSetSummaryOut):
    changes: tuple[ChangeOut, ...]


class ChangeSetPageOut(BaseModel):
    items: list[ChangeSetSummaryOut]
    next_cursor: str | None
    total: int


class ChangeDecisionIn(BaseModel):
    change_id: int
    decision: Literal["pending", "accepted", "rejected"]
    new_value: object | None = None


class ApplyDecisionsRequest(BaseModel):
    decisions: list[ChangeDecisionIn]


class ApplyResultOut(BaseModel):
    change_set_id: int
    state: str
    applied_track_ids: list[int]
    conflicted_track_ids: list[int]
    errors: dict[int, str]


class TrackPatchRequest(BaseModel):
    """PATCH /api/tracks/{id} body: canonical field name -> new value.
    Creates a DRAFT ChangeSet per docs/PLAN.md §10, never writes
    directly."""

    fields: dict[str, object]


class BulkEditFieldIn(BaseModel):
    field: str
    new_value: object


class BulkEditRequest(BaseModel):
    track_ids: list[int]
    fields: list[BulkEditFieldIn]


class FindReplaceRequest(BaseModel):
    track_ids: list[int]
    field: str
    find: str
    replace: str
    use_regex: bool = False


class FindReplacePreviewRowOut(BaseModel):
    track_id: int
    old_value: str
    new_value: str


class FindReplacePreviewOut(BaseModel):
    rows: list[FindReplacePreviewRowOut]


class StripRequest(BaseModel):
    track_ids: list[int]
