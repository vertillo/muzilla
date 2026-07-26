"""change_sets, changes, apply_journal, and blobs — the staged-changes machinery

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "change_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_ref", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="draft"),
        sa.Column("scope_type", sa.String(), nullable=False, server_default="track"),
        sa.Column("scope_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False, server_default="web"),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("candidate_source", sa.String(), nullable=True),
        sa.Column("candidate_ref", sa.String(), nullable=True),
        sa.Column(
            "undo_of_id",
            sa.Integer(),
            sa.ForeignKey("change_sets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_change_sets_state", "change_sets", ["state"])

    op.create_table(
        "changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "change_set_id",
            sa.Integer(),
            sa.ForeignKey("change_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entity_type", sa.String(), nullable=False, server_default="track"),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("op", sa.String(), nullable=False, server_default="set"),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("old_blob_id", sa.Integer(), nullable=True),
        sa.Column("new_blob_id", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("severity", sa.String(), nullable=False, server_default="normal"),
        sa.Column("decision", sa.String(), nullable=False, server_default="pending"),
        sa.Column("apply_state", sa.String(), nullable=False, server_default="pending"),
        sa.Column("is_manual", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_changes_change_set_id", "changes", ["change_set_id"])
    op.create_index("ix_changes_entity", "changes", ["entity_type", "entity_id"])

    op.create_table(
        "apply_journal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "change_set_id",
            sa.Integer(),
            sa.ForeignKey("change_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "track_id",
            sa.Integer(),
            sa.ForeignKey("tracks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False, server_default="tags"),
        sa.Column("state", sa.String(), nullable=False, server_default="pending"),
        sa.Column("before_hash", sa.String(), nullable=True),
        sa.Column("after_hash", sa.String(), nullable=True),
        sa.Column("before_blob", sa.JSON(), nullable=False),
        sa.Column("before_path", sa.String(), nullable=True),
        sa.Column("after_path", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_apply_journal_change_set_id", "apply_journal", ["change_set_id"])

    op.create_table(
        "blobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("mime", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("refcount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_blobs_sha256", "blobs", ["sha256"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_blobs_sha256", table_name="blobs")
    op.drop_table("blobs")
    op.drop_index("ix_apply_journal_change_set_id", table_name="apply_journal")
    op.drop_table("apply_journal")
    op.drop_index("ix_changes_entity", table_name="changes")
    op.drop_index("ix_changes_change_set_id", table_name="changes")
    op.drop_table("changes")
    op.drop_index("ix_change_sets_state", table_name="change_sets")
    op.drop_table("change_sets")
