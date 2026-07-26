"""provider_cache — semantic cache of normalized provider results

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("query_hash", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_provider_cache_provider", "provider_cache", ["provider"])
    op.create_index(
        "uq_provider_cache_key",
        "provider_cache",
        ["provider", "operation", "query_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_provider_cache_key", table_name="provider_cache")
    op.drop_index("ix_provider_cache_provider", table_name="provider_cache")
    op.drop_table("provider_cache")
