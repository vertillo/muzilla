"""Wire contract for the ReviewBundle foundation.

The operation union is deliberately discriminated by ``kind``.  Consumers can exhaust
the known write vocabulary and, notably, cannot mistake a structured lyrics payload for
an arbitrary display string.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


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
    created_at: datetime
    operations: tuple[ReviewOperationOut, ...]


class OperationAttemptOut(BaseModel):
    operation_id: int
    attempted_value: object | None
    state: Literal["pending", "applied", "failed", "conflicted", "skipped"]
    error: str | None


class ApplyRunOut(BaseModel):
    id: int
    revision_id: int
    state: Literal["pending", "applying", "applied", "partially_applied", "failed"]
    result: dict[str, object] | None
    error: str | None
    operation_attempts: tuple[OperationAttemptOut, ...]


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
    apply_runs: tuple[ApplyRunOut, ...]
