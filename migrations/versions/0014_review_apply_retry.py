"""Allow a failed/partial bundle to resume its frozen apply manifest.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FORWARD_TRIGGER = """CREATE TRIGGER review_bundles_state_transition
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
        ('applied', 'partially_applied', 'failed')) OR
    (OLD.state IN ('partially_applied', 'failed') AND NEW.state = 'applying')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid review bundle state transition');
END"""


_LEGACY_TRIGGER = """CREATE TRIGGER review_bundles_state_transition
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


def upgrade() -> None:
    op.execute("DROP TRIGGER review_bundles_state_transition")
    op.execute(_FORWARD_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER review_bundles_state_transition")
    op.execute(_LEGACY_TRIGGER)
