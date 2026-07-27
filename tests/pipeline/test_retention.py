from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from muzilla.db.models import ApplyJournal, ChangeSet, ProviderCache, Track
from muzilla.pipeline.retention import (
    run_retention_sweep,
    sweep_apply_journals,
    sweep_provider_cache,
)


def _make_track(session: Session, *, path: str = "/a.mp3") -> Track:
    t = Track(path=path, filename=path.rsplit("/", 1)[-1], ext="mp3", size_bytes=1000, mtime_ns=1)
    session.add(t)
    session.flush()
    return t


def _make_applied_changeset_with_journal(
    session: Session, track: Track, *, journal_created_at: datetime
) -> ChangeSet:
    cs = ChangeSet(title="Edit", source="manual_edit", state="applied")
    session.add(cs)
    session.flush()
    journal = ApplyJournal(
        change_set_id=cs.id,
        track_id=track.id,
        path=track.path,
        phase="tags",
        state="done",
        before_hash="h1",
        after_hash="h2",
        before_blob={"title": "old"},
    )
    session.add(journal)
    session.flush()
    # created_at has a server-side default; overwrite it directly to
    # simulate an old journal without waiting real time in tests.
    journal.created_at = journal_created_at
    session.flush()
    return cs


def test_sweep_prunes_journals_past_age_threshold(db_session: Session) -> None:
    track = _make_track(db_session)
    old_cutoff = datetime.now(UTC) - timedelta(days=31)
    cs = _make_applied_changeset_with_journal(db_session, track, journal_created_at=old_cutoff)
    db_session.commit()

    pruned, marked = sweep_apply_journals(db_session, journal_days=30, journal_changesets=500)
    db_session.commit()

    assert pruned == 1
    assert marked == 1
    assert db_session.query(ApplyJournal).filter_by(change_set_id=cs.id).count() == 0
    db_session.refresh(cs)
    assert cs.state == "undo_expired"


def test_sweep_keeps_journals_within_age_threshold(db_session: Session) -> None:
    track = _make_track(db_session)
    recent = datetime.now(UTC) - timedelta(days=1)
    cs = _make_applied_changeset_with_journal(db_session, track, journal_created_at=recent)
    db_session.commit()

    pruned, marked = sweep_apply_journals(db_session, journal_days=30, journal_changesets=500)
    db_session.commit()

    assert pruned == 0
    assert marked == 0
    assert db_session.query(ApplyJournal).filter_by(change_set_id=cs.id).count() == 1
    db_session.refresh(cs)
    assert cs.state == "applied"


def test_sweep_prunes_past_count_threshold_even_when_recent(db_session: Session) -> None:
    """Three changesets, all created "now" (well within the age
    threshold) but journal_changesets=2 -- the oldest-by-journal-time
    one must still be pruned, proving the count threshold fires
    independently of age."""
    now = datetime.now(UTC)
    changesets = []
    for i in range(3):
        track = _make_track(db_session, path=f"/track-{i}.mp3")
        # stagger by seconds so ordering is deterministic; oldest first
        cs = _make_applied_changeset_with_journal(
            db_session, track, journal_created_at=now - timedelta(seconds=3 - i)
        )
        changesets.append(cs)
    db_session.commit()

    pruned, marked = sweep_apply_journals(db_session, journal_days=30, journal_changesets=2)
    db_session.commit()

    assert pruned == 1
    assert marked == 1
    oldest_cs = changesets[0]
    db_session.refresh(oldest_cs)
    assert oldest_cs.state == "undo_expired"
    assert db_session.query(ApplyJournal).filter_by(change_set_id=oldest_cs.id).count() == 0
    for cs in changesets[1:]:
        db_session.refresh(cs)
        assert cs.state == "applied"


def test_sweep_does_not_mark_draft_changeset_expired(db_session: Session) -> None:
    """A journal orphaned onto a still-draft changeset (shouldn't
    normally happen, but the sweep must not clobber state on anything
    that was never applied) is pruned without a state transition."""
    track = _make_track(db_session)
    cs = ChangeSet(title="Draft", source="manual_edit", state="draft")
    db_session.add(cs)
    db_session.flush()
    journal = ApplyJournal(
        change_set_id=cs.id,
        track_id=track.id,
        path=track.path,
        phase="tags",
        state="done",
        before_blob={},
    )
    db_session.add(journal)
    db_session.flush()
    journal.created_at = datetime.now(UTC) - timedelta(days=31)
    db_session.commit()

    pruned, marked = sweep_apply_journals(db_session, journal_days=30, journal_changesets=500)
    db_session.commit()

    assert pruned == 1
    assert marked == 0
    db_session.refresh(cs)
    assert cs.state == "draft"


def test_sweep_provider_cache_prunes_only_expired_rows(db_session: Session) -> None:
    expired = ProviderCache(
        provider="musicbrainz",
        operation="get_release",
        query_hash="h1",
        payload={},
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    fresh = ProviderCache(
        provider="musicbrainz",
        operation="get_release",
        query_hash="h2",
        payload={},
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add_all([expired, fresh])
    db_session.commit()

    pruned_count = sweep_provider_cache(db_session)
    db_session.commit()

    assert pruned_count == 1
    remaining = db_session.query(ProviderCache).all()
    assert len(remaining) == 1
    assert remaining[0].query_hash == "h2"


def test_run_retention_sweep_combines_both_sweeps(db_session: Session) -> None:
    track = _make_track(db_session)
    old_cutoff = datetime.now(UTC) - timedelta(days=31)
    _make_applied_changeset_with_journal(db_session, track, journal_created_at=old_cutoff)
    db_session.add(
        ProviderCache(
            provider="deezer",
            operation="search_releases",
            query_hash="h3",
            payload={},
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.commit()

    result = run_retention_sweep(db_session, journal_days=30, journal_changesets=500)
    db_session.commit()

    assert result.journals_pruned == 1
    assert result.changesets_marked_expired == 1
    assert result.provider_cache_rows_pruned == 1
