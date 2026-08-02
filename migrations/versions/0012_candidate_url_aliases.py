"""Persist non-semantic candidate URL aliases outside proposal revision content.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_url_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "proposal_revision_id",
            sa.Integer(),
            sa.ForeignKey("proposal_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("candidate_type", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "candidate_type IN ('release', 'album', 'track')",
            name="ck_candidate_url_aliases_type",
        ),
        sa.UniqueConstraint(
            "proposal_revision_id",
            "provider",
            "candidate_type",
            "provider_id",
            name="uq_candidate_url_aliases_revision_ref",
        ),
    )


def downgrade() -> None:
    op.drop_table("candidate_url_aliases")
