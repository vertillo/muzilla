"""Import-owned ReviewBundles and indexed inbox projection.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite can add these columns in place.  Avoid batch recreation here: existing
    # ReviewBundle state-machine triggers refer to this table and must stay live.
    op.execute(
        "ALTER TABLE review_bundles ADD COLUMN import_session_id "
        "INTEGER REFERENCES import_sessions(id) ON DELETE SET NULL"
    )
    op.create_index("ix_review_bundles_import_session_id", "review_bundles", ["import_session_id"])
    op.add_column("proposal_revisions", sa.Column("candidate_snapshot", sa.JSON(), nullable=True))
    op.add_column("proposal_revisions", sa.Column("match_explanation", sa.JSON(), nullable=True))
    op.add_column("proposal_revisions", sa.Column("confidence", sa.Float(), nullable=True))

    # The existing immutable-content trigger predates the candidate evidence.  Recreate
    # it so a revision cannot be cosmetically rewritten after an import/restart.
    op.execute("DROP TRIGGER proposal_revisions_content_immutable")
    op.execute(
        """CREATE TRIGGER proposal_revisions_content_immutable
        BEFORE UPDATE OF review_bundle_id, source_snapshot_id, revision_no,
                         parent_revision_no, content_digest, candidate_source,
                         candidate_ref, candidate_snapshot, match_explanation,
                         confidence, created_at
        ON proposal_revisions
        BEGIN
            SELECT RAISE(ABORT, 'proposal revision content is immutable');
        END"""
    )

    op.create_table(
        "review_inbox_entries",
        sa.Column(
            "review_bundle_id",
            sa.Integer(),
            sa.ForeignKey("review_bundles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("logical_key", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filename", sa.String(), nullable=True),
        sa.Column("path", sa.String(), nullable=True),
        sa.Column("format", sa.String(), nullable=True),
        sa.Column("candidate_source", sa.String(), nullable=True),
        sa.Column("candidate_ref", sa.String(), nullable=True),
        sa.Column("candidate_title", sa.String(), nullable=True),
        sa.Column("candidate_artist", sa.String(), nullable=True),
        sa.Column("candidate_album", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confidence_label", sa.String(), nullable=False),
        sa.Column("issue_kind", sa.String(), nullable=True),
        sa.Column("issue_message", sa.String(), nullable=True),
        sa.Column("accepted_operations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_operations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_operations", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_review_inbox_entries_priority",
        "review_inbox_entries",
        ["state", "updated_at", "review_bundle_id"],
    )
    op.execute(
        """CREATE VIRTUAL TABLE review_inbox_entries_fts USING fts5(
            title, filename, path, candidate_source, candidate_title,
            candidate_artist, candidate_album, issue_message,
            content='review_inbox_entries', content_rowid='review_bundle_id'
        )"""
    )
    op.execute(
        """CREATE TRIGGER review_inbox_entries_ai AFTER INSERT ON review_inbox_entries BEGIN
            INSERT INTO review_inbox_entries_fts(rowid, title, filename, path, candidate_source,
                candidate_title, candidate_artist, candidate_album, issue_message)
            VALUES (NEW.review_bundle_id, NEW.title, NEW.filename, NEW.path, NEW.candidate_source,
                NEW.candidate_title, NEW.candidate_artist, NEW.candidate_album, NEW.issue_message);
        END"""
    )
    op.execute(
        """CREATE TRIGGER review_inbox_entries_ad AFTER DELETE ON review_inbox_entries BEGIN
            INSERT INTO review_inbox_entries_fts(review_inbox_entries_fts, rowid, title, filename, path,
                candidate_source, candidate_title, candidate_artist, candidate_album, issue_message)
            VALUES ('delete', OLD.review_bundle_id, OLD.title, OLD.filename, OLD.path, OLD.candidate_source,
                OLD.candidate_title, OLD.candidate_artist, OLD.candidate_album, OLD.issue_message);
        END"""
    )
    op.execute(
        """CREATE TRIGGER review_inbox_entries_au AFTER UPDATE ON review_inbox_entries BEGIN
            INSERT INTO review_inbox_entries_fts(review_inbox_entries_fts, rowid, title, filename, path,
                candidate_source, candidate_title, candidate_artist, candidate_album, issue_message)
            VALUES ('delete', OLD.review_bundle_id, OLD.title, OLD.filename, OLD.path, OLD.candidate_source,
                OLD.candidate_title, OLD.candidate_artist, OLD.candidate_album, OLD.issue_message);
            INSERT INTO review_inbox_entries_fts(rowid, title, filename, path, candidate_source,
                candidate_title, candidate_artist, candidate_album, issue_message)
            VALUES (NEW.review_bundle_id, NEW.title, NEW.filename, NEW.path, NEW.candidate_source,
                NEW.candidate_title, NEW.candidate_artist, NEW.candidate_album, NEW.issue_message);
        END"""
    )
    # Rebuild the projection for existing ReviewBundles so an upgrade preserves
    # inbox visibility. Subsequent writes use the service upsert in the same transaction.
    op.execute(
        """INSERT INTO review_inbox_entries (
            review_bundle_id, state, title, logical_key, updated_at, filename, path, format,
            candidate_source, candidate_ref, candidate_title, candidate_artist, candidate_album,
            confidence, confidence_label, issue_kind, issue_message,
            accepted_operations, pending_operations, rejected_operations
        )
        SELECT b.id, b.state, b.title, b.logical_key, b.updated_at,
            json_extract(s.payload, '$.items[0].filename'),
            json_extract(s.payload, '$.items[0].path'),
            CASE WHEN instr(coalesce(json_extract(s.payload, '$.items[0].filename'), ''), '.') > 0
                THEN lower(substr(json_extract(s.payload, '$.items[0].filename'),
                    instr(json_extract(s.payload, '$.items[0].filename'), '.') + 1)) END,
            p.candidate_source, p.candidate_ref,
            json_extract(p.candidate_snapshot, '$.title'),
            json_extract(p.candidate_snapshot, '$.artist'),
            json_extract(p.candidate_snapshot, '$.album'),
            p.confidence,
            CASE
                WHEN p.candidate_snapshot IS NOT NULL AND p.confidence IS NULL THEN 'Manual selection'
                WHEN p.confidence >= 0.85 THEN 'High confidence'
                WHEN p.confidence >= 0.65 THEN 'Medium confidence'
                WHEN p.confidence IS NOT NULL THEN 'Low confidence'
                WHEN b.state = 'needs_attention' THEN 'Needs attention'
                WHEN b.state = 'preparing' THEN 'Preparing'
                ELSE 'Not scored'
            END,
            CASE WHEN b.error IS NOT NULL THEN 'review' END, b.error,
            (SELECT count(*) FROM operations o WHERE o.proposal_revision_id = p.id AND o.decision = 'accepted'),
            (SELECT count(*) FROM operations o WHERE o.proposal_revision_id = p.id AND o.decision = 'pending'),
            (SELECT count(*) FROM operations o WHERE o.proposal_revision_id = p.id AND o.decision = 'rejected')
        FROM review_bundles b
        JOIN proposal_revisions p ON p.review_bundle_id = b.id AND p.is_current = 1
        JOIN source_snapshots s ON s.id = p.source_snapshot_id"""
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER review_inbox_entries_au")
    op.execute("DROP TRIGGER review_inbox_entries_ad")
    op.execute("DROP TRIGGER review_inbox_entries_ai")
    op.execute("DROP TABLE review_inbox_entries_fts")
    op.drop_index("ix_review_inbox_entries_priority", table_name="review_inbox_entries")
    op.drop_table("review_inbox_entries")
    op.execute("DROP TRIGGER proposal_revisions_content_immutable")
    op.execute(
        """CREATE TRIGGER proposal_revisions_content_immutable
        BEFORE UPDATE OF review_bundle_id, source_snapshot_id, revision_no,
                         parent_revision_no, content_digest, candidate_source,
                         candidate_ref, created_at
        ON proposal_revisions
        BEGIN
            SELECT RAISE(ABORT, 'proposal revision content is immutable');
        END"""
    )
    op.drop_column("proposal_revisions", "confidence")
    op.drop_column("proposal_revisions", "match_explanation")
    op.drop_column("proposal_revisions", "candidate_snapshot")
    op.drop_index("ix_review_bundles_import_session_id", table_name="review_bundles")
    op.drop_column("review_bundles", "import_session_id")
