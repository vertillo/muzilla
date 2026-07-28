"""schema_meta.auth_epoch — session revocation on logout (docs/PLAN.md §12c)

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "schema_meta",
        sa.Column("auth_epoch", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("schema_meta", "auth_epoch")
