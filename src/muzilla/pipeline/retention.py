"""Retention sweep for review journals and the provider cache.

Review file journals with ``before_blob`` are the undo mechanism's raw material.
Two independent thresholds prune whichever fires first:

- age: journal older than ``journal_days``
- count: journal's owning ApplyRun is not among the most-recently-created
  ApplyRuns that have any journal at all

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.db.models import ProviderCache, ReviewFileJournal, ReviewUndoRun
from muzilla.db.transactions import begin_sqlite_write_transaction


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


def _is_true(value: object) -> bool:
    return isinstance(value, bool) and value


def _undo_run_needs_journals(run: ReviewUndoRun) -> bool:
    if run.state in {"pending", "undoing"}:
        return True
    if run.state != "failed":
        return False
    result = run.result if isinstance(run.result, dict) else {}
    if any(_is_true(result.get(key)) for key in ("recovery_required", "cancelled", "retryable")):
        return True
    files = run.manifest.get("files", []) if isinstance(run.manifest, dict) else []
    return isinstance(files, list) and any(
        isinstance(entry, dict) and _is_true(entry.get("retryable")) for entry in files
    )


def sweep_apply_journals(
    session: Session, *, journal_days: int, journal_changesets: int
) -> tuple[int, int]:
    """Prunes ReviewFileJournal rows past either threshold."""
    from muzilla.db.models import ApplyRun

    begin_sqlite_write_transaction(session)
    age_cutoff = datetime.now(UTC) - timedelta(days=journal_days)
    count_expired_ids = _apply_run_ids_beyond_count_threshold(session, keep_runs=journal_changesets)
    age_expired = list(
        session.scalars(select(ReviewFileJournal).where(ReviewFileJournal.created_at < age_cutoff))
    )
    to_prune = {journal.id: journal for journal in age_expired}
    if count_expired_ids:
        count_expired = list(
            session.scalars(
                select(ReviewFileJournal).where(
                    ReviewFileJournal.apply_run_id.in_(count_expired_ids)
                )
            )
        )
        for journal in count_expired:
            to_prune[journal.id] = journal
    if not to_prune:
        return 0, 0

    # Fresh protection reads and deletion happen under the same SQLite writer
    # reservation as enqueue/retry, so a new Undo cannot lose journals in flight.
    protected_ids: set[int] = set()
    for run in session.scalars(select(ApplyRun).where(ApplyRun.state.in_(["applying", "pending"]))):
        protected_ids.add(run.id)
    for run in session.scalars(select(ApplyRun)):
        result = run.result
        if isinstance(result, dict) and _is_true(result.get("recovery_required")):
            protected_ids.add(run.id)
    for undo_run in session.scalars(select(ReviewUndoRun)):
        if _undo_run_needs_journals(undo_run):
            protected_ids.add(undo_run.source_apply_run_id)
    to_prune = {
        journal_id: journal
        for journal_id, journal in to_prune.items()
        if journal.apply_run_id not in protected_ids
    }
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
    journals_pruned, changesets_marked = sweep_apply_journals(
        session, journal_days=journal_days, journal_changesets=journal_changesets
    )
    provider_cache_pruned = sweep_provider_cache(session)
    return RetentionResult(
        journals_pruned=journals_pruned,
        changesets_marked_expired=changesets_marked,
        provider_cache_rows_pruned=provider_cache_pruned,
    )
