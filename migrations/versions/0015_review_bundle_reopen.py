"""Allow an archived ReviewBundle to reopen when a decision is changed.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_REOPEN_TRIGGER = """CREATE TRIGGER review_bundles_state_transition
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
    (OLD.state IN ('partially_applied', 'failed') AND NEW.state = 'applying') OR
    (OLD.state = 'discarded' AND NEW.state IN ('ready', 'needs_attention'))
)
BEGIN
    SELECT RAISE(ABORT, 'invalid review bundle state transition');
END"""


_PREVIOUS_TRIGGER = """CREATE TRIGGER review_bundles_state_transition
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


def upgrade() -> None:
    op.execute("DROP TRIGGER review_bundles_state_transition")
    op.execute(_REOPEN_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER review_bundles_state_transition")
    op.execute(_PREVIOUS_TRIGGER)
