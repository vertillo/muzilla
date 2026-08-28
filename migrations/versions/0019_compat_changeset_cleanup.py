"""COMPAT-CHANGESET-001 cleanup: drop legacy ChangeSet tables, create ReviewFileJournal.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-28

- Drops legacy tables: change_sets, changes, apply_journal (and their indexes/FKs).
- Creates review_file_journals for native ReviewBundle writer.
- Clean-database path: running all migrations from scratch creates only the new journal.
- Supported upgrade: existing DBs with legacy tables are migrated by dropping them;
  pre-production ChangeSet state is not preserved (per matrix). No music files are touched.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop legacy ChangeSet machinery if present (upgrade path from 0003).
    # Use batch operations where needed for SQLite FK handling.
    # Order: children first (apply_journal, changes) then parent (change_sets, blobs refs remain)
    # apply_journal was created in 0003 and may have index ix_apply_journal_change_set_id
    for table in ("apply_journal", "changes", "change_sets"):
        with contextlib.suppress(Exception):
            op.drop_table(table)

    # Create native ReviewFileJournal (ponytail: minimal durable journal tied to ApplyRun)
    op.create_table(
        "review_file_journals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "apply_run_id",
            sa.Integer(),
            sa.ForeignKey("apply_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "track_id",
            sa.Integer(),
            sa.ForeignKey("tracks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False, server_default="tags"),
        sa.Column("state", sa.String(), nullable=False, server_default="pending"),
        sa.Column("before_hash", sa.String(), nullable=True),
        sa.Column("after_hash", sa.String(), nullable=True),
        sa.Column("before_blob", sa.JSON(), nullable=False),
        sa.Column("before_path", sa.String(), nullable=True),
        sa.Column("after_path", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_review_file_journals_apply_run_id",
        "review_file_journals",
        ["apply_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_file_journals_apply_run_id", table_name="review_file_journals")
    op.drop_table("review_file_journals")
    # Downgrade does not recreate legacy tables (one-way migration per matrix).
    # If needed, restore from 0003/0018 baseline and re-run upgrade.
