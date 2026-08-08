"""Associate validated cover assets with their owning review.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "review_bundle_id",
            sa.Integer(),
            sa.ForeignKey("review_bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "blob_id",
            sa.Integer(),
            sa.ForeignKey("blobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "provider <> ''", name="ck_asset_candidates_provider_not_empty"
        ),
        sa.UniqueConstraint(
            "review_bundle_id", "blob_id", name="uq_asset_candidates_bundle_blob"
        ),
    )
    op.create_index(
        "ix_asset_candidates_review_bundle_id",
        "asset_candidates",
        ["review_bundle_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_asset_candidates_review_bundle_id", table_name="asset_candidates")
    op.drop_table("asset_candidates")
