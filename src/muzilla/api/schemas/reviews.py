"""Wire contract for the ReviewBundle foundation.

The operation union is deliberately discriminated by ``kind``.  Consumers can exhaust
the known write vocabulary and, notably, cannot mistake a structured lyrics payload for
an arbitrary display string.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LyricsValueOut(BaseModel):
    text: str
    synced: bool
    provider: str


class ArtBlobRefOut(BaseModel):
    blob_id: int


class OperationBaseOut(BaseModel):
    id: int
    seq: int
    field: str
    target_type: str
    target_id: int
    current_value: object | None
    proposed_value: object | None
    decision: Literal["pending", "accepted", "rejected"]
    provenance: dict[str, object]
    validation: dict[str, object]


class SetTagOperationOut(OperationBaseOut):
    kind: Literal["set_tag"]


class WriteLyricsOperationOut(OperationBaseOut):
    kind: Literal["write_lyrics"]
    current_value: LyricsValueOut | None
    proposed_value: LyricsValueOut


class EmbedArtOperationOut(OperationBaseOut):
    kind: Literal["embed_art"]
    current_value: ArtBlobRefOut | None
    proposed_value: ArtBlobRefOut


class RemoveArtOperationOut(OperationBaseOut):
    kind: Literal["remove_art"]
    current_value: ArtBlobRefOut | None
    proposed_value: None = None


class MoveFileOperationOut(OperationBaseOut):
    kind: Literal["move_file"]
    current_value: str
    proposed_value: str


class SetReplayGainOperationOut(OperationBaseOut):
    kind: Literal["set_replay_gain"]
    current_value: float | None
    proposed_value: float


class GroupingCorrectionOperationOut(OperationBaseOut):
    kind: Literal["grouping_correction"]


type ReviewOperationOut = Annotated[
    SetTagOperationOut
    | WriteLyricsOperationOut
    | EmbedArtOperationOut
    | RemoveArtOperationOut
    | MoveFileOperationOut
    | SetReplayGainOperationOut
    | GroupingCorrectionOperationOut,
    Field(discriminator="kind"),
]


class ProposalRevisionOut(BaseModel):
    id: int
    revision_no: int
    content_digest: str
    candidate_source: str | None
    candidate_ref: str | None
    candidate_snapshot: dict[str, object] | None
    match_explanation: dict[str, object] | None
    confidence: float | None
    created_at: datetime
    operations: tuple[ReviewOperationOut, ...]


class OperationAttemptOut(BaseModel):
    operation_id: int
    attempted_value: object | None
    state: Literal["pending", "applied", "failed", "conflicted", "skipped"]
    error: str | None


class FileApplyResultOut(BaseModel):
    track_id: int
    state: Literal["applied", "failed", "skipped"]
    applied_operation_ids: tuple[int, ...]
    error: str | None


class BundleApplyResultOut(BaseModel):
    state: Literal["applied", "partially_applied", "failed"]
    atomicity: Literal["per_file"]
    files: tuple[FileApplyResultOut, ...]


class ApplyRunOut(BaseModel):
    id: int
    revision_id: int
    state: Literal["pending", "applying", "applied", "partially_applied", "failed"]
    result: BundleApplyResultOut | None
    error: str | None
    operation_attempts: tuple[OperationAttemptOut, ...]


class FileUndoResultOut(BaseModel):
    track_id: int
    state: Literal["undone", "failed", "pending"]
    source_change_set_ids: tuple[int, ...]
    error: str | None
    retryable: bool


class BundleUndoResultOut(BaseModel):
    state: Literal["undone", "partially_undone", "failed"]
    atomicity: Literal["per_file"]
    files: tuple[FileUndoResultOut, ...]


class ReviewUndoRunOut(BaseModel):
    id: int
    source_apply_run_id: int
    state: Literal["pending", "undoing", "undone", "partially_undone", "failed"]
    result: BundleUndoResultOut | None
    error: str | None
    job_ids: tuple[int, ...]


class TaskAttemptOut(BaseModel):
    id: int
    kind: str
    item_key: str
    state: Literal[
        "pending",
        "running",
        "succeeded",
        "not_found",
        "transient_failure",
        "permanent_failure",
        "cancelled",
    ]
    attempt_no: int
    job_id: int | None
    result: dict[str, object] | None
    error: str | None


class AssetCandidateOut(BaseModel):
    id: int
    blob_id: int
    provider: str
    mime: Literal["image/jpeg", "image/png"]
    size: int
    width: int
    height: int
    thumbnail_url: str


class CoverDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["keep", "select", "remove"]
    asset_candidate_id: int | None = None

    @model_validator(mode="after")
    def validate_candidate_reference(self) -> CoverDecisionRequest:
        if self.action == "select" and self.asset_candidate_id is None:
            raise ValueError("select requires asset_candidate_id")
        if self.action != "select" and self.asset_candidate_id is not None:
            raise ValueError("asset_candidate_id is valid only for select")
        return self


class ApplyReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backup: bool | None = None


class ApplyReviewOut(BaseModel):
    apply_run_id: int
    job_id: int


class UndoReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apply_run_id: int
    backup: bool | None = None


class UndoReviewOut(BaseModel):
    undo_run_id: int
    job_id: int


class ReviewBundleDetailOut(BaseModel):
    id: int
    logical_key: str
    title: str
    scope_type: str
    scope_id: int | None
    state: Literal[
        "preparing",
        "ready",
        "needs_attention",
        "applying",
        "applied",
        "partially_applied",
        "failed",
        "discarded",
    ]
    error: str | None
    current_revision: ProposalRevisionOut
    source_items: tuple[SourceFileSummaryOut, ...]
    cover_candidates: tuple[AssetCandidateOut, ...]
    task_attempts: tuple[TaskAttemptOut, ...]
    apply_runs: tuple[ApplyRunOut, ...]
    undo_runs: tuple[ReviewUndoRunOut, ...]


class SourceFileSummaryOut(BaseModel):
    """Immutable source identity captured when the review was prepared."""

    source_id: int | None
    filename: str | None
    path: str | None
    format: str | None
    art_blob_id: int | None
    cover_thumbnail_url: str | None


class ReviewIssueOut(BaseModel):
    kind: Literal["review", "task", "collision"]
    message: str


class ReviewBundleSummaryOut(BaseModel):
    id: int
    title: str
    state: Literal[
        "preparing",
        "ready",
        "needs_attention",
        "applying",
        "applied",
        "partially_applied",
        "failed",
        "discarded",
    ]
    filename: str | None
    path: str | None
    format: str | None
    candidate_source: str | None
    confidence: float | None
    confidence_label: str
    cover_thumbnail_url: str | None
    issues: tuple[ReviewIssueOut, ...]
    accepted_operations: int
    pending_operations: int
    rejected_operations: int


class ReviewBundlePageOut(BaseModel):
    items: tuple[ReviewBundleSummaryOut, ...]
    next_cursor: str | None
    total: int


class ReviewNeighborsOut(BaseModel):
    previous_id: int | None
    next_id: int | None
    next_unreviewed_id: int | None


class ReviewOperationDecisionIn(BaseModel):
    operation_id: int
    decision: Literal["pending", "accepted", "rejected"]


class ReviewOperationDecisionsRequest(BaseModel):
    revision_id: int
    decisions: tuple[ReviewOperationDecisionIn, ...]


class EditSetTagOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: int
    kind: Literal["set_tag"]
    value: object | None


class EditWriteLyricsOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: int
    kind: Literal["write_lyrics"]
    text: str
    synced: bool


type ReviewOperationEditRequest = Annotated[
    EditSetTagOperationRequest | EditWriteLyricsOperationRequest,
    Field(discriminator="kind"),
]
