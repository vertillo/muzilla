"""Pydantic schemas for /api/paths (docs/product-spec.md, §10).

§10 lists `POST /api/paths/preview {template, album_id} -> rendered
paths` as the representative spec; this module generalizes `album_id`
to `group_id`/`track_ids` to match services/paths.py's actual
preview_rename/stage_rename signature (§10 is "representative", not a
literal wire contract, and the service already supports both a single
group and an arbitrary track set). Not stated in either §6 or §10: a
`/api/paths/rename` endpoint that stages the DRAFT ChangeSet — added
here mirroring the existing /tracks/strip and /tracks/find-replace
shape (preview endpoint + a same-body apply endpoint that returns the
staged ChangeSet for review in the existing ChangeSetReview UI).
"""

from __future__ import annotations

from pydantic import BaseModel


class PathPreviewRequest(BaseModel):
    track_ids: list[int] | None = None
    group_id: int | None = None
    template: str | None = None
    """Overrides the configured album/singleton/query template for this
    preview only -- does not persist. None uses the configured template
    (config/schema.py's PathsConfig), same as stage_rename's default."""


class PathPreviewRowOut(BaseModel):
    track_id: int
    old_path: str
    new_path: str
    errors: tuple[str, ...]
    is_collision: bool


class PathPreviewOut(BaseModel):
    rows: list[PathPreviewRowOut]


class PathRenameRequest(BaseModel):
    track_ids: list[int] | None = None
    group_id: int | None = None
    template: str | None = None
