"""Pydantic schemas for /api/groups (the grouping workspace, §9)."""

from __future__ import annotations

from pydantic import BaseModel


class GroupSummaryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    key: str
    kind: str
    grouping_basis: str | None
    grouping_confidence: float | None
    is_pinned: bool
    album: str | None
    album_artist: str | None
    year: int | None
    track_count: int
    expected_track_count: int | None
    match_state: str


class GroupDetailOut(GroupSummaryOut):
    track_ids: tuple[int, ...]


class GroupListOut(BaseModel):
    items: list[GroupSummaryOut]


class RunCascadeResultOut(BaseModel):
    groups_created: int
    groups_updated: int
    tracks_grouped: int
    tracks_skipped_pinned: int


class MergeGroupsRequest(BaseModel):
    from_group_ids: list[int]


class SplitGroupRequest(BaseModel):
    track_ids: list[int]


class ReassignTrackRequest(BaseModel):
    track_id: int
    to_group_id: int


class ForceSingletonRequest(BaseModel):
    track_id: int
