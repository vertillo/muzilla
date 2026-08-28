"""Retention sweep for review journals and the provider cache.

Review file journals with ``before_blob`` are the undo mechanism's raw material.
Two independent thresholds prune whichever fires first:

- age: journal older than ``journal_days``
- count: journal's owning ApplyRun is not among the most-recently-created
  ApplyRuns that have any journal at all

# ponytail: minimal retention on ReviewFileJournal; mark expired via ApplyRun
# state not needed because undo expiry is via journal age. Upgrade path: mark
# undo horizon explicitly if needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.db.models import ProviderCache, ReviewFileJournal


@dataclass(frozen=True, slots=True)
class RetentionResult:
    journals_pruned: int = 0
    changesets_marked_expired: int = 0
    provider_cache_rows_pruned: int = 0


def _apply_run_ids_beyond_count_threshold(session: Session, *, keep_runs: int) -> set[int]:
    ranked = (
        select(
            ReviewFileJournal.apply_run_id,
            func.min(ReviewFileJournal.created_at).label("first_journal_at"),
        )
        .group_by(ReviewFileJournal.apply_run_id)
        .order_by(func.min(ReviewFileJournal.created_at).desc())
    )
    rows = list(session.execute(ranked))
    beyond = rows[keep_runs:]
    return {row.apply_run_id for row in beyond}


def sweep_apply_journals(
    session: Session, *, journal_days: int, journal_changesets: int
) -> tuple[int, int]:
    """Prunes ReviewFileJournal rows past either threshold."""
    from muzilla.db.models import ApplyRun

    age_cutoff = datetime.now(UTC) - timedelta(days=journal_days)
    count_expired_ids = _apply_run_ids_beyond_count_threshold(session, keep_runs=journal_changesets)
    # Exclude journals needed for recovery: applying runs or recovery_required
    # ponytail: protect recovery-required journals from pruning
    protected_ids: set[int] = set()
    for run in session.scalars(select(ApplyRun).where(ApplyRun.state.in_(["applying", "pending"]))):
        protected_ids.add(run.id)
    # also protect runs with recovery_required flag in result
    for run in session.scalars(select(ApplyRun)):
        result = run.result
        if isinstance(result, dict) and result.get("recovery_required") is True:
            protected_ids.add(run.id)
    age_expired = list(session.scalars(select(ReviewFileJournal).where(ReviewFileJournal.created_at < age_cutoff)))
    to_prune = {j.id: j for j in age_expired if j.apply_run_id not in protected_ids}
    if count_expired_ids:
        count_expired = list(session.scalars(select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id.in_(count_expired_ids))))
        for j in count_expired:
            if j.apply_run_id not in protected_ids:
                to_prune[j.id] = j
    if not to_prune:
        return 0, 0
    for journal in to_prune.values():
        session.delete(journal)
    session.flush()
    return len(to_prune), 0


def sweep_provider_cache(session: Session) -> int:
    now = datetime.now(UTC)
    expired = list(session.scalars(select(ProviderCache).where(ProviderCache.expires_at < now)))
    for row in expired:
        session.delete(row)
    if expired:
        session.flush()
    return len(expired)


def run_retention_sweep(
    session: Session, *, journal_days: int, journal_changesets: int
) -> RetentionResult:
    journals_pruned, changesets_marked = sweep_apply_journals(session, journal_days=journal_days, journal_changesets=journal_changesets)
    provider_cache_pruned = sweep_provider_cache(session)
    return RetentionResult(journals_pruned=journals_pruned, changesets_marked_expired=changesets_marked, provider_cache_rows_pruned=provider_cache_pruned)
