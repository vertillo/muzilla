"""ReviewBundle, immutable revisions, typed operation storage, and apply attempts.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-01

The migration is additive and does not rewrite existing ChangeSets. Existing drafts and
apply/undo history remain readable while newer producers use ReviewBundle tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_bundles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("logical_key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(), nullable=False, server_default="preparing"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('preparing', 'ready', 'needs_attention', 'applying', "
            "'applied', 'partially_applied', 'failed', 'discarded')",
            name="ck_review_bundles_state",
        ),
    )
    op.create_index("ix_review_bundles_state", "review_bundles", ["state"])
    op.create_index(
        "uq_review_bundles_active_logical_key",
        "review_bundles",
        ["logical_key"],
        unique=True,
        sqlite_where=sa.text(
            "state IN ('preparing', 'ready', 'needs_attention', 'applying')"
        ),
    )

    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "review_bundle_id",
            sa.Integer(),
            sa.ForeignKey("review_bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_digest", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "review_bundle_id", "content_digest", name="uq_source_snapshots_bundle_digest"
        ),
    )

    op.create_table(
        "proposal_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "review_bundle_id",
            sa.Integer(),
            sa.ForeignKey("review_bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_snapshot_id",
            sa.Integer(),
            sa.ForeignKey("source_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("parent_revision_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_digest", sa.String(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("candidate_source", sa.String(), nullable=True),
        sa.Column("candidate_ref", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "review_bundle_id", "revision_no", name="uq_proposal_revisions_bundle_number"
        ),
        sa.UniqueConstraint(
            "review_bundle_id",
            "parent_revision_no",
            "content_digest",
            name="uq_proposal_revisions_idempotent_successor",
        ),
    )
    op.create_index(
        "uq_proposal_revisions_current_bundle",
        "proposal_revisions",
        ["review_bundle_id"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
    )

    op.create_table(
        "operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "proposal_revision_id",
            sa.Integer(),
            sa.ForeignKey("proposal_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_snapshot_id",
            sa.Integer(),
            sa.ForeignKey("source_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("current_value", sa.JSON(), nullable=True),
        sa.Column("proposed_value", sa.JSON(), nullable=True),
        sa.Column("decision", sa.String(), nullable=False, server_default="pending"),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('set_tag', 'write_lyrics', 'embed_art', 'remove_art', "
            "'move_file', 'set_replay_gain', 'grouping_correction')",
            name="ck_operations_kind",
        ),
        sa.CheckConstraint(
            "decision IN ('pending', 'accepted', 'rejected')",
            name="ck_operations_decision",
        ),
        sa.CheckConstraint(
            "kind != 'write_lyrics' OR ("
            "COALESCE(json_type(proposed_value), '') = 'object' AND "
            "COALESCE(json_type(proposed_value, '$.text'), '') = 'text' AND "
            "COALESCE(json_type(proposed_value, '$.synced'), '') IN ('true', 'false') AND "
            "COALESCE(json_type(proposed_value, '$.provider'), '') = 'text' AND "
            "(COALESCE(json_type(current_value), 'null') = 'null' OR ("
            "COALESCE(json_type(current_value), '') = 'object' AND "
            "COALESCE(json_type(current_value, '$.text'), '') = 'text' AND "
            "COALESCE(json_type(current_value, '$.synced'), '') IN ('true', 'false') AND "
            "COALESCE(json_type(current_value, '$.provider'), '') = 'text')))",
            name="ck_operations_write_lyrics_value",
        ),
        sa.CheckConstraint(
            "kind != 'embed_art' OR ("
            "COALESCE(json_type(proposed_value), '') = 'object' AND "
            "COALESCE(json_type(proposed_value, '$.blob_id'), '') = 'integer' AND "
            "(COALESCE(json_type(current_value), 'null') = 'null' OR ("
            "COALESCE(json_type(current_value), '') = 'object' AND "
            "COALESCE(json_type(current_value, '$.blob_id'), '') = 'integer')))",
            name="ck_operations_embed_art_value",
        ),
        sa.CheckConstraint(
            "kind != 'remove_art' OR ("
            "COALESCE(json_type(proposed_value), 'null') = 'null' AND "
            "(COALESCE(json_type(current_value), 'null') = 'null' OR ("
            "COALESCE(json_type(current_value), '') = 'object' AND "
            "COALESCE(json_type(current_value, '$.blob_id'), '') = 'integer')))",
            name="ck_operations_remove_art_value",
        ),
        sa.CheckConstraint(
            "kind != 'move_file' OR ("
            "COALESCE(json_type(current_value), '') = 'text' AND "
            "COALESCE(json_type(proposed_value), '') = 'text')",
            name="ck_operations_move_file_value",
        ),
        sa.CheckConstraint(
            "kind != 'set_replay_gain' OR ("
            "COALESCE(json_type(proposed_value), '') IN ('integer', 'real') AND "
            "COALESCE(json_type(current_value), 'null') IN ('null', 'integer', 'real'))",
            name="ck_operations_replay_gain_value",
        ),
        sa.UniqueConstraint("proposal_revision_id", "seq", name="uq_operations_revision_seq"),
    )
    op.create_index(
        "ix_operations_proposal_revision_id", "operations", ["proposal_revision_id"]
    )

    op.create_table(
        "task_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "review_bundle_id",
            sa.Integer(),
            sa.ForeignKey("review_bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proposal_revision_id",
            sa.Integer(),
            sa.ForeignKey("proposal_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("item_key", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'succeeded', 'not_found', "
            "'transient_failure', 'permanent_failure', 'cancelled')",
            name="ck_task_attempts_state",
        ),
        sa.CheckConstraint("attempt_no >= 1", name="ck_task_attempts_attempt_no"),
        sa.UniqueConstraint(
            "review_bundle_id",
            "kind",
            "item_key",
            "attempt_no",
            name="uq_task_attempts_bundle_kind_item_number",
        ),
    )
    op.create_index(
        "ix_task_attempts_bundle_state", "task_attempts", ["review_bundle_id", "state"]
    )

    op.create_table(
        "apply_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "review_bundle_id",
            sa.Integer(),
            sa.ForeignKey("review_bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proposal_revision_id",
            sa.Integer(),
            sa.ForeignKey("proposal_revisions.id", ondelete="RESTRICT"),
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
            "state IN ('pending', 'applying', 'applied', 'partially_applied', 'failed')",
            name="ck_apply_runs_state",
        ),
        sa.UniqueConstraint(
            "review_bundle_id", "idempotency_key", name="uq_apply_runs_bundle_idempotency"
        ),
    )
    op.create_index(
        "uq_apply_runs_active_bundle",
        "apply_runs",
        ["review_bundle_id"],
        unique=True,
        sqlite_where=sa.text("state IN ('pending', 'applying')"),
    )

    op.create_table(
        "operation_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "apply_run_id",
            sa.Integer(),
            sa.ForeignKey("apply_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "operation_id",
            sa.Integer(),
            sa.ForeignKey("operations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempted_value", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(), nullable=False, server_default="pending"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'applied', 'failed', 'conflicted', 'skipped')",
            name="ck_operation_attempts_state",
        ),
        sa.UniqueConstraint("apply_run_id", "operation_id", name="uq_operation_attempts_run_op"),
    )

    # Snapshot/proposal content is audit evidence.  Only user decisions and the
    # current-revision marker are mutable; attempted values are frozen per ApplyRun.
    op.execute(
        """CREATE TRIGGER source_snapshots_immutable
        BEFORE UPDATE ON source_snapshots
        BEGIN
            SELECT RAISE(ABORT, 'source snapshots are immutable');
        END"""
    )
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
    op.execute(
        """CREATE TRIGGER operations_content_immutable
        BEFORE UPDATE OF proposal_revision_id, source_snapshot_id, seq, kind, field,
                         target_type, target_id, current_value, proposed_value,
                         provenance, validation, created_at
        ON operations
        BEGIN
            SELECT RAISE(ABORT, 'operation proposal content is immutable');
        END"""
    )
    op.execute(
        """CREATE TRIGGER operation_attempts_value_immutable
        BEFORE UPDATE OF apply_run_id, operation_id, attempted_value, created_at
        ON operation_attempts
        BEGIN
            SELECT RAISE(ABORT, 'operation attempted value is immutable');
        END"""
    )
    op.execute(
        """CREATE TRIGGER review_bundles_state_transition
        BEFORE UPDATE OF state ON review_bundles
        WHEN NOT (
            OLD.state = NEW.state OR
            (OLD.state = 'preparing' AND NEW.state IN
                ('ready', 'needs_attention', 'failed', 'discarded')) OR
            (OLD.state = 'ready' AND NEW.state IN
                ('needs_attention', 'applying', 'discarded')) OR
            (OLD.state = 'needs_attention' AND NEW.state IN
                ('ready', 'applying', 'discarded')) OR
            (OLD.state = 'applying' AND NEW.state IN
                ('applied', 'partially_applied', 'failed'))
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid review bundle state transition');
        END"""
    )
    op.execute(
        """CREATE TRIGGER review_bundles_visible_requires_current
        BEFORE UPDATE OF state ON review_bundles
        WHEN NEW.state IN ('ready', 'needs_attention', 'applying')
             AND NOT EXISTS (
                SELECT 1 FROM proposal_revisions
                WHERE review_bundle_id = NEW.id AND is_current = 1
             )
        BEGIN
            SELECT RAISE(ABORT, 'visible review bundle requires a current revision');
        END"""
    )
    op.execute(
        """CREATE TRIGGER review_bundles_apply_requires_accepted
        BEFORE UPDATE OF state ON review_bundles
        WHEN NEW.state = 'applying'
             AND NOT EXISTS (
                SELECT 1
                FROM operations AS operation
                JOIN proposal_revisions AS revision
                  ON revision.id = operation.proposal_revision_id
                WHERE revision.review_bundle_id = NEW.id
                  AND revision.is_current = 1
                  AND operation.decision = 'accepted'
             )
        BEGIN
            SELECT RAISE(ABORT, 'review bundle has no accepted operations');
        END"""
    )
    op.execute(
        """CREATE TRIGGER proposal_revisions_snapshot_scope_insert
        BEFORE INSERT ON proposal_revisions
        WHEN (SELECT review_bundle_id FROM source_snapshots WHERE id = NEW.source_snapshot_id)
             IS NOT NEW.review_bundle_id
        BEGIN
            SELECT RAISE(ABORT, 'proposal revision snapshot belongs to another bundle');
        END"""
    )
    op.execute(
        """CREATE TRIGGER proposal_revisions_current_frozen
        BEFORE UPDATE OF is_current ON proposal_revisions
        WHEN (SELECT state FROM review_bundles WHERE id = NEW.review_bundle_id)
             NOT IN ('preparing', 'ready', 'needs_attention')
        BEGIN
            SELECT RAISE(ABORT, 'current revision is frozen after review');
        END"""
    )
    # A visible bundle replaces its current revision by first clearing the old row.
    # Permit that write only when the immutable immediate successor already exists,
    # then promote that successor in the same statement so zero-current is never
    # externally commit-able.
    op.execute(
        """CREATE TRIGGER proposal_revisions_current_required_update
        BEFORE UPDATE OF is_current ON proposal_revisions
        WHEN OLD.is_current = 1
             AND NEW.is_current = 0
             AND (SELECT state FROM review_bundles WHERE id = NEW.review_bundle_id)
                 IN ('ready', 'needs_attention')
             AND NOT EXISTS (
                SELECT 1
                FROM proposal_revisions AS successor
                WHERE successor.review_bundle_id = OLD.review_bundle_id
                  AND successor.parent_revision_no = OLD.revision_no
                  AND successor.revision_no = OLD.revision_no + 1
                  AND successor.is_current = 0
             )
        BEGIN
            SELECT RAISE(ABORT, 'visible review bundle requires a current revision');
        END"""
    )
    op.execute(
        """CREATE TRIGGER proposal_revisions_promote_successor
        AFTER UPDATE OF is_current ON proposal_revisions
        WHEN OLD.is_current = 1
             AND NEW.is_current = 0
             AND (SELECT state FROM review_bundles WHERE id = NEW.review_bundle_id)
                 IN ('ready', 'needs_attention')
        BEGIN
            UPDATE proposal_revisions
            SET is_current = 1
            WHERE review_bundle_id = OLD.review_bundle_id
              AND parent_revision_no = OLD.revision_no
              AND revision_no = OLD.revision_no + 1;
        END"""
    )
    op.execute(
        """CREATE TRIGGER proposal_revisions_current_required_delete
        BEFORE DELETE ON proposal_revisions
        WHEN OLD.is_current = 1
             AND (SELECT state FROM review_bundles WHERE id = OLD.review_bundle_id)
                 != 'preparing'
        BEGIN
            SELECT RAISE(ABORT, 'review bundle cannot delete its current revision');
        END"""
    )
    op.execute(
        """CREATE TRIGGER operations_snapshot_scope_insert
        BEFORE INSERT ON operations
        WHEN (SELECT review_bundle_id FROM source_snapshots WHERE id = NEW.source_snapshot_id)
             IS NOT
             (SELECT review_bundle_id FROM proposal_revisions
              WHERE id = NEW.proposal_revision_id)
        BEGIN
            SELECT RAISE(ABORT, 'operation snapshot belongs to another bundle');
        END"""
    )
    op.execute(
        """CREATE TRIGGER operations_decision_mutable_current
        BEFORE UPDATE OF decision ON operations
        WHEN (SELECT is_current FROM proposal_revisions
              WHERE id = NEW.proposal_revision_id) IS NOT 1
             OR (SELECT bundle.state
                 FROM proposal_revisions AS revision
                 JOIN review_bundles AS bundle ON bundle.id = revision.review_bundle_id
                 WHERE revision.id = NEW.proposal_revision_id)
                NOT IN ('preparing', 'ready', 'needs_attention')
        BEGIN
            SELECT RAISE(ABORT, 'operation decision is frozen');
        END"""
    )
    op.execute(
        """CREATE TRIGGER apply_runs_revision_scope_insert
        BEFORE INSERT ON apply_runs
        WHEN (SELECT review_bundle_id FROM proposal_revisions
              WHERE id = NEW.proposal_revision_id) IS NOT NEW.review_bundle_id
        BEGIN
            SELECT RAISE(ABORT, 'apply run revision belongs to another bundle');
        END"""
    )
    op.execute(
        """CREATE TRIGGER task_attempts_revision_scope_insert
        BEFORE INSERT ON task_attempts
        WHEN NEW.proposal_revision_id IS NOT NULL
             AND (SELECT review_bundle_id FROM proposal_revisions
                  WHERE id = NEW.proposal_revision_id) IS NOT NEW.review_bundle_id
        BEGIN
            SELECT RAISE(ABORT, 'task attempt revision belongs to another bundle');
        END"""
    )
    op.execute(
        """CREATE TRIGGER operation_attempts_revision_scope_insert
        BEFORE INSERT ON operation_attempts
        WHEN (SELECT proposal_revision_id FROM operations WHERE id = NEW.operation_id)
             IS NOT
             (SELECT proposal_revision_id FROM apply_runs WHERE id = NEW.apply_run_id)
        BEGIN
            SELECT RAISE(ABORT, 'operation attempt belongs to another revision');
        END"""
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER operation_attempts_revision_scope_insert")
    op.execute("DROP TRIGGER task_attempts_revision_scope_insert")
    op.execute("DROP TRIGGER apply_runs_revision_scope_insert")
    op.execute("DROP TRIGGER operations_decision_mutable_current")
    op.execute("DROP TRIGGER operations_snapshot_scope_insert")
    op.execute("DROP TRIGGER proposal_revisions_current_required_delete")
    op.execute("DROP TRIGGER proposal_revisions_promote_successor")
    op.execute("DROP TRIGGER proposal_revisions_current_required_update")
    op.execute("DROP TRIGGER proposal_revisions_current_frozen")
    op.execute("DROP TRIGGER proposal_revisions_snapshot_scope_insert")
    op.execute("DROP TRIGGER review_bundles_apply_requires_accepted")
    op.execute("DROP TRIGGER review_bundles_visible_requires_current")
    op.execute("DROP TRIGGER review_bundles_state_transition")
    op.execute("DROP TRIGGER operation_attempts_value_immutable")
    op.execute("DROP TRIGGER operations_content_immutable")
    op.execute("DROP TRIGGER proposal_revisions_content_immutable")
    op.execute("DROP TRIGGER source_snapshots_immutable")
    op.drop_table("operation_attempts")
    op.drop_index("uq_apply_runs_active_bundle", table_name="apply_runs")
    op.drop_table("apply_runs")
    op.drop_index("ix_task_attempts_bundle_state", table_name="task_attempts")
    op.drop_table("task_attempts")
    op.drop_index("ix_operations_proposal_revision_id", table_name="operations")
    op.drop_table("operations")
    op.drop_index("uq_proposal_revisions_current_bundle", table_name="proposal_revisions")
    op.drop_table("proposal_revisions")
    op.drop_table("source_snapshots")
    op.drop_index("uq_review_bundles_active_logical_key", table_name="review_bundles")
    op.drop_index("ix_review_bundles_state", table_name="review_bundles")
    op.drop_table("review_bundles")
