"""Provider cache versioning for PIPELINE-CACHE-001.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-01

Adds schema_version to provider_cache for versioned entry handling.
Existing rows get version 1, matching CACHE_VERSION.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "provider_cache",
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("provider_cache", "schema_version", server_default=None)


def downgrade() -> None:
    op.drop_column("provider_cache", "schema_version")
