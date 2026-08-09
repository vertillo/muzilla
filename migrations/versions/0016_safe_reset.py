"""Persistent maintenance lock and destructive-operation audit.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("request_digest", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="running"),
        sa.Column("phase", sa.String(), nullable=False, server_default="prepared"),
        sa.Column("actor", sa.String(), nullable=False, server_default="single-user"),
        sa.Column("outcome", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_admin_operations_idempotency_key"
        ),
    )
    op.create_index(
        "ix_admin_operations_state_created",
        "admin_operations",
        ["state", "created_at"],
    )
    op.create_table(
        "system_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "maintenance_mode", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "active_operation_id",
            sa.Integer(),
            sa.ForeignKey("admin_operations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("system_state")
    op.drop_index("ix_admin_operations_state_created", table_name="admin_operations")
    op.drop_table("admin_operations")
