"""Persistent ReviewBundle undo runs.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_undo_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "review_bundle_id",
            sa.Integer(),
            sa.ForeignKey("review_bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_apply_run_id",
            sa.Integer(),
            sa.ForeignKey("apply_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="pending"),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'undoing', 'undone', 'partially_undone', 'failed')",
            name="ck_review_undo_runs_state",
        ),
        sa.UniqueConstraint(
            "source_apply_run_id", name="uq_review_undo_runs_source_apply_run"
        ),
        sa.UniqueConstraint(
            "review_bundle_id",
            "idempotency_key",
            name="uq_review_undo_runs_bundle_idempotency",
        ),
    )
    op.create_index(
        "uq_review_undo_runs_active_bundle",
        "review_undo_runs",
        ["review_bundle_id"],
        unique=True,
        sqlite_where=sa.text("state IN ('pending', 'undoing')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_review_undo_runs_active_bundle", table_name="review_undo_runs"
    )
    op.drop_table("review_undo_runs")
