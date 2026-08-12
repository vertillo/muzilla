"""Retention sweep for apply journals and the provider cache.

Journals with `before_blob` are the undo mechanism's raw material —
keeping them forever is unbounded growth for a table nobody reads after
the undo window closes. Two independent thresholds prune whichever fires
first:

- age: journal older than `journal_days`
- count: journal's owning ChangeSet is not among the `journal_changesets`
  most-recently-created ChangeSets that have any journal at all

Pruning a journal does not delete the ChangeSet or its Changes — only
the ApplyJournal rows. The owning ChangeSet is
marked `state="undo_expired"`, which is sufficient on its own to block
undo (changes/undo.py checks `state in ("applied", "partially_applied")`)
and to hide the Undo button (ChangesList.tsx checks the same two
values) — no separate flag is needed.

Blob refcounts are untouched by this sweep: ApplyJournal holds no blob
reference (art blob ids live on
Track.art_blob_id and Change.old_blob_id/new_blob_id, both already
refcounted by changes/applier.py's _rebalance_art_refcounts), so there
is nothing here for changes/blobstore.py's release() to release.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.db.models import ApplyJournal, ChangeSet, ProviderCache


@dataclass(frozen=True, slots=True)
class RetentionResult:
    journals_pruned: int = 0
    changesets_marked_expired: int = 0
    provider_cache_rows_pruned: int = 0


def _changeset_ids_beyond_count_threshold(session: Session, *, keep_changesets: int) -> set[int]:
    """ChangeSet ids, among those with at least one journal, that fall
    outside the `keep_changesets` most-recently-created — these are
    prune candidates under the count threshold regardless of age."""
    ranked = (
        select(
            ApplyJournal.change_set_id,
            func.min(ApplyJournal.created_at).label("first_journal_at"),
        )
        .group_by(ApplyJournal.change_set_id)
        .order_by(func.min(ApplyJournal.created_at).desc())
    )
    rows = list(session.execute(ranked))
    beyond_threshold = rows[keep_changesets:]
    return {row.change_set_id for row in beyond_threshold}


def sweep_apply_journals(
    session: Session, *, journal_days: int, journal_changesets: int
) -> tuple[int, int]:
    """Prunes ApplyJournal rows past either threshold, marking each
    newly-expired ChangeSet `undo_expired`. Returns
    (journals_pruned, changesets_marked_expired). Does not commit —
    caller controls the transaction, matching every other pipeline/
    module's convention."""
    age_cutoff = datetime.now(UTC) - timedelta(days=journal_days)
    count_expired_ids = _changeset_ids_beyond_count_threshold(
        session, keep_changesets=journal_changesets
    )

    age_expired = list(
        session.scalars(select(ApplyJournal).where(ApplyJournal.created_at < age_cutoff))
    )
    to_prune = {j.id: j for j in age_expired}

    if count_expired_ids:
        count_expired = list(
            session.scalars(
                select(ApplyJournal).where(ApplyJournal.change_set_id.in_(count_expired_ids))
            )
        )
        to_prune.update({j.id: j for j in count_expired})

    if not to_prune:
        return 0, 0

    affected_change_set_ids = {j.change_set_id for j in to_prune.values()}
    for journal in to_prune.values():
        session.delete(journal)
    session.flush()

    marked = 0
    for cs_id in affected_change_set_ids:
        cs = session.get(ChangeSet, cs_id)
        if cs is not None and cs.state in ("applied", "partially_applied"):
            cs.state = "undo_expired"
            marked += 1
    session.flush()

    return len(to_prune), marked


def sweep_provider_cache(session: Session) -> int:
    """Deletes ProviderCache rows past their own `expires_at` — a
    per-row TTL the semantic cache already computes at write time
    (providers/cache.py), never swept until now."""
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
