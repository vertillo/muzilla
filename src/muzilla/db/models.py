"""SQLAlchemy 2.0 declarative models.

Phase 0 seeds the base + a `schema_version` marker so Alembic has a
baseline to migrate from. Tracks/track_groups/changes/etc. land in
Phases 1-2 per docs/PLAN.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SchemaMeta(Base):
    """Single-row marker table confirming migrations have run."""

    __tablename__ = "schema_meta"

    id: Mapped[int] = mapped_column(primary_key=True)
    initialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
