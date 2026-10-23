from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from muzilla.db.engine import create_session_factory
from muzilla.db.models import ProviderCache
from muzilla.db.transactions import begin_sqlite_write_transaction
from muzilla.pipeline.retention import sweep_provider_cache
from muzilla.providers.cache import cache_get_fresh, cache_put


def test_provider_cache_sweep_keeps_stale_grace_and_prunes_older_rows(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            ProviderCache(
                provider="musicbrainz",
                operation="search_releases",
                query_hash="fresh",
                payload=[],
                expires_at=now + timedelta(days=1),
            ),
            ProviderCache(
                provider="musicbrainz",
                operation="search_releases",
                query_hash="within-stale-grace",
                payload=[],
                expires_at=now - timedelta(days=29),
            ),
            ProviderCache(
                provider="musicbrainz",
                operation="search_releases",
                query_hash="past-stale-grace",
                payload=[],
                expires_at=now - timedelta(days=31),
            ),
        ]
    )
    db_session.commit()

    assert sweep_provider_cache(db_session) == 1
    remaining = set(db_session.scalars(select(ProviderCache.query_hash)))
    assert remaining == {"fresh", "within-stale-grace"}


def test_provider_cache_sweep_deletion_rolls_back_with_caller_transaction(
    db_session: Session,
) -> None:
    row = ProviderCache(
        provider="musicbrainz",
        operation="search_releases",
        query_hash="rollback-prune",
        payload=[],
        expires_at=datetime.now(UTC) - timedelta(days=31),
    )
    db_session.add(row)
    db_session.commit()

    assert sweep_provider_cache(db_session) == 1
    db_session.rollback()
    assert (
        db_session.scalar(
            select(ProviderCache.id).where(ProviderCache.query_hash == "rollback-prune")
        )
        == row.id
    )


def test_provider_cache_sweep_serializes_with_cache_refresh(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import muzilla.pipeline.retention as retention

    key = "refresh-wins-sweep"
    db_session.add(
        ProviderCache(
            provider="musicbrainz",
            operation="search_releases",
            query_hash=key,
            payload={"version": "old"},
            expires_at=datetime.now(UTC) - timedelta(days=31),
        )
    )
    db_session.commit()
    engine = db_session.get_bind()
    assert isinstance(engine, Engine)
    session_factory = create_session_factory(engine)
    refreshed = threading.Event()
    sweep_started = threading.Event()
    release_refresh = threading.Event()
    errors: list[Exception] = []
    results: dict[str, int] = {}

    def pause_sweep_until_refresh_commits(session: Session) -> None:
        sweep_started.set()
        begin_sqlite_write_transaction(session)

    def refresh_worker() -> None:
        with session_factory() as session:
            try:
                cache_put(
                    session,
                    "musicbrainz",
                    "search_releases",
                    key,
                    {"version": "fresh"},
                )
                refreshed.set()
                if not release_refresh.wait(timeout=5):
                    raise TimeoutError("test did not release the cache refresh transaction")
                session.commit()
            except Exception as exc:
                session.rollback()
                errors.append(exc)

    def sweep_worker() -> None:
        with session_factory() as session:
            try:
                results["pruned"] = retention.sweep_provider_cache(session)
                session.commit()
            except Exception as exc:
                session.rollback()
                errors.append(exc)

    with monkeypatch.context() as patch:
        patch.setattr(
            retention, "begin_sqlite_write_transaction", pause_sweep_until_refresh_commits
        )
        refresh_thread = threading.Thread(target=refresh_worker)
        sweep_thread = threading.Thread(target=sweep_worker)
        refresh_thread.start()
        assert refreshed.wait(timeout=5)
        sweep_thread.start()
        try:
            assert sweep_started.wait(timeout=5)
        finally:
            release_refresh.set()
            refresh_thread.join(timeout=10)
            sweep_thread.join(timeout=10)
        assert not refresh_thread.is_alive() and not sweep_thread.is_alive()

    assert not errors, errors
    assert results == {"pruned": 0}
    with session_factory() as session:
        refreshed_row = session.scalar(select(ProviderCache).where(ProviderCache.query_hash == key))
        assert refreshed_row is not None
        assert refreshed_row.payload == {"version": "fresh"}


def test_provider_cache_sweep_first_serializes_cache_refresh(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import muzilla.providers.cache as provider_cache

    key = "sweep-wins-refresh"
    db_session.add(
        ProviderCache(
            provider="musicbrainz",
            operation="search_releases",
            query_hash=key,
            payload={"version": "stale"},
            expires_at=datetime.now(UTC) - timedelta(days=31),
        )
    )
    db_session.commit()
    engine = db_session.get_bind()
    assert isinstance(engine, Engine)
    session_factory = create_session_factory(engine)
    sweep_session = session_factory()
    refresh_activity = threading.Event()
    reservation_requested = threading.Event()
    reservation_acquired = threading.Event()
    cache_write_phase = threading.Event()
    selects_before_reservation: list[str] = []
    errors: list[Exception] = []
    refresh_thread: threading.Thread | None = None

    def tracked_begin(session: Session) -> None:
        if threading.current_thread().name == "cache-refresh":
            reservation_requested.set()
            refresh_activity.set()
        begin_sqlite_write_transaction(session)
        if threading.current_thread().name == "cache-refresh":
            reservation_acquired.set()

    def observe_refresh_select(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            threading.current_thread().name == "cache-refresh"
            and cache_write_phase.is_set()
            and statement.lstrip().upper().startswith("SELECT")
        ):
            if not reservation_acquired.is_set():
                selects_before_reservation.append(statement)
            refresh_activity.set()

    def refresh_after_sweep_worker() -> None:
        with session_factory() as session:
            try:
                assert cache_get_fresh(session, "musicbrainz", "search_releases", key) is None
                cache_write_phase.set()
                cache_put(
                    session,
                    "musicbrainz",
                    "search_releases",
                    key,
                    {"version": "fresh"},
                )
                session.commit()
            except Exception as exc:
                session.rollback()
                errors.append(exc)

    event.listen(engine, "before_cursor_execute", observe_refresh_select)
    try:
        assert sweep_provider_cache(sweep_session) == 1
        with monkeypatch.context() as patch:
            patch.setattr(
                provider_cache,
                "begin_sqlite_write_transaction",
                tracked_begin,
            )
            refresh_thread = threading.Thread(
                target=refresh_after_sweep_worker, name="cache-refresh"
            )
            refresh_thread.start()
            try:
                assert refresh_activity.wait(timeout=5)
                assert reservation_requested.is_set(), (
                    "refresh read the row before requesting SQLite's writer reservation"
                )
            finally:
                sweep_session.commit()
                refresh_thread.join(timeout=10)
        assert not refresh_thread.is_alive()
        assert reservation_acquired.is_set()
        assert not selects_before_reservation
        assert not errors, errors
        with session_factory() as session:
            refreshed_row = session.scalar(
                select(ProviderCache).where(ProviderCache.query_hash == key)
            )
            assert refreshed_row is not None
            assert refreshed_row.payload == {"version": "fresh"}
    finally:
        if sweep_session.in_transaction():
            sweep_session.rollback()
        if refresh_thread is not None and refresh_thread.is_alive():
            refresh_thread.join(timeout=10)
        event.remove(engine, "before_cursor_execute", observe_refresh_select)
        sweep_session.close()
