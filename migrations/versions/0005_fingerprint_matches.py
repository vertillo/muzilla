"""track_fingerprint_matches — persisted AcoustID lookup results

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "track_fingerprint_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "track_id",
            sa.Integer(),
            sa.ForeignKey("tracks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mb_recording_id", sa.String(), nullable=False),
        sa.Column("mb_release_ids", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_track_fingerprint_matches_track_id", "track_fingerprint_matches", ["track_id"]
    )
    op.create_index(
        "ix_track_fingerprint_matches_mb_recording_id",
        "track_fingerprint_matches",
        ["mb_recording_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_track_fingerprint_matches_mb_recording_id", table_name="track_fingerprint_matches"
    )
    op.drop_index(
        "ix_track_fingerprint_matches_track_id", table_name="track_fingerprint_matches"
    )
    op.drop_table("track_fingerprint_matches")
