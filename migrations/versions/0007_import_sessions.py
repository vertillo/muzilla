"""import_sessions, import_tasks — resumable scan/fingerprint/group/
match orchestration; change_sets.import_session_id links a match job's
proposals back to the session that produced them.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("library_root", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="pending"),
        sa.Column(
            "job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "import_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "import_session_id", sa.Integer(),
            sa.ForeignKey("import_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="pending"),
        sa.Column(
            "job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_import_tasks_session_id", "import_tasks", ["import_session_id"])

    with op.batch_alter_table("change_sets") as batch_op:
        batch_op.add_column(sa.Column("import_session_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_change_sets_import_session_id",
            "import_sessions",
            ["import_session_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("change_sets") as batch_op:
        batch_op.drop_constraint("fk_change_sets_import_session_id", type_="foreignkey")
        batch_op.drop_column("import_session_id")
    op.drop_index("ix_import_tasks_session_id", table_name="import_tasks")
    op.drop_table("import_tasks")
    op.drop_table("import_sessions")
