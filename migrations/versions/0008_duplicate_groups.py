"""duplicate_groups, duplicate_members — fingerprint-based duplicate
detection (docs/PLAN.md §Phase-6)

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "duplicate_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mb_recording_id", sa.String(), nullable=False, unique=True),
        sa.Column("basis", sa.String(), nullable=False, server_default="acoustid"),
        sa.Column("dismissed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_duplicate_groups_mb_recording_id", "duplicate_groups", ["mb_recording_id"]
    )

    op.create_table(
        "duplicate_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("duplicate_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "track_id",
            sa.Integer(),
            sa.ForeignKey("tracks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("group_id", "track_id", name="uq_duplicate_member"),
    )
    op.create_index("ix_duplicate_members_group_id", "duplicate_members", ["group_id"])
    op.create_index("ix_duplicate_members_track_id", "duplicate_members", ["track_id"])


def downgrade() -> None:
    op.drop_index("ix_duplicate_members_track_id", table_name="duplicate_members")
    op.drop_index("ix_duplicate_members_group_id", table_name="duplicate_members")
    op.drop_table("duplicate_members")
    op.drop_index("ix_duplicate_groups_mb_recording_id", table_name="duplicate_groups")
    op.drop_table("duplicate_groups")
