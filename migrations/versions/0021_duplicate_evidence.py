"""Duplicate evidence with calibrated confidence.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-02

Adds confidence and evidence JSON to duplicate_groups.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("duplicate_groups", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("duplicate_groups", sa.Column("evidence", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("duplicate_groups", "evidence")
    op.drop_column("duplicate_groups", "confidence")
