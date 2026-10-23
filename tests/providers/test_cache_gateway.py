"""PIPELINE-CACHE-001 gateway integration: versioned cache, TTL, refresh, stale fallback, offline, restart."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import ProviderCache
from muzilla.matching.candidates import retrieve_and_hydrate
from muzilla.providers.base import ProviderHealth, ProviderRef, ReleaseCandidate, ReleaseQuery
from muzilla.providers.cache import cache_get, cache_put, query_hash


class _FakeProvider:
    name: str
    requires_auth: bool = False
    capabilities: frozenset = frozenset()  # type: ignore[type-arg]  # set in __init__

    def __init__(
        self, name: str, candidates: list[ReleaseCandidate] | None = None, fail: bool = False
    ):
        from muzilla.providers.base import Capability

        self.name = name
        self._candidates = candidates or []
        self.fail = fail
        self.search_calls = 0
        self.hydrate_calls = 0
        self.capabilities = frozenset({Capability.SEARCH_RELEASES, Capability.GET_RELEASE})

    async def search_releases(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]:
        self.search_calls += 1
        if self.fail:
            raise RuntimeError("provider failure")
        return self._candidates[:limit]

    async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None:
        self.hydrate_calls += 1
        if self.fail:
            raise RuntimeError("hydrate failure")
        for c in self._candidates:
            if c.ref.id == ref.id:
                return c
        return None

    async def health(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, healthy=True)


def _make_candidate(source: str, ref_id: str) -> ReleaseCandidate:
    return ReleaseCandidate(
        source=source,
        ref=ProviderRef(provider=source, id=ref_id),
        album="Album",
        album_artist="Artist",
        tracks=(),
    )


@pytest.mark.asyncio
async def test_gateway_caches_search_and_reuses_on_second_call(db_session: Session) -> None:
    query = ReleaseQuery(album="Test", album_artist="Artist")
    cand = _make_candidate("musicbrainz", "mb-1")
    provider = _FakeProvider("musicbrainz", [cand])
    config = Config()
    # First call: network, then cache.
    result1 = await retrieve_and_hydrate(
        query, {"musicbrainz": provider}, session=db_session, config=config
    )
    assert len(result1.candidates) == 1
    assert provider.search_calls == 1
    # Second call: should hit fresh cache, no additional network call.
    provider.search_calls = 0
    result2 = await retrieve_and_hydrate(
        query, {"musicbrainz": provider}, session=db_session, config=config
    )
    assert len(result2.candidates) == 1
    assert provider.search_calls == 0
    # Provider outcomes should indicate cache.
    assert any(o.detail == "cache" for o in result2.provider_outcomes)


@pytest.mark.asyncio
async def test_gateway_falls_back_to_stale_on_provider_failure(db_session: Session) -> None:
    query = ReleaseQuery(album="Stale", album_artist="Artist")
    cand = _make_candidate("musicbrainz", "mb-stale")
    # Prime cache with a successful call.
    provider_ok = _FakeProvider("musicbrainz", [cand])
    config = Config()
    await retrieve_and_hydrate(
        query, {"musicbrainz": provider_ok}, session=db_session, config=config
    )
    # Expire the search cache to make it stale, then provider fails should fallback to stale.
    rows = list(
        db_session.scalars(
            select(ProviderCache).where(ProviderCache.operation == "search_releases")
        )
    )
    assert rows, "search cache should exist"
    for r in rows:
        r.expires_at = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()
    provider_fail = _FakeProvider("musicbrainz", fail=True)
    result = await retrieve_and_hydrate(
        query, {"musicbrainz": provider_fail}, session=db_session, config=config
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].ref.id == "mb-stale"
    assert any("stale" in (o.detail or "") for o in result.provider_outcomes)


@pytest.mark.asyncio
async def test_offline_mode_does_not_discover_never_retrieved(db_session: Session) -> None:
    query = ReleaseQuery(album="Never", album_artist="Artist")
    cand = _make_candidate("musicbrainz", "mb-never")
    provider = _FakeProvider("musicbrainz", [cand])
    config = Config(providers_offline=True)
    result = await retrieve_and_hydrate(
        query, {"musicbrainz": provider}, session=db_session, config=config
    )
    assert len(result.candidates) == 0
    assert provider.search_calls == 0
    # Should be zero_results, not failed, and no discovery.
    assert any(o.status == "zero_results" for o in result.provider_outcomes)


@pytest.mark.asyncio
async def test_manual_refresh_bypasses_fresh_cache_but_still_falls_back_to_stale(
    db_session: Session,
) -> None:
    query = ReleaseQuery(album="Refresh", album_artist="Artist")
    cand_old = _make_candidate("musicbrainz", "mb-old")
    cand_new = _make_candidate("musicbrainz", "mb-new")
    provider_old = _FakeProvider("musicbrainz", [cand_old])
    config = Config()
    # Prime with old.
    await retrieve_and_hydrate(
        query, {"musicbrainz": provider_old}, session=db_session, config=config
    )
    # Refresh with new provider that returns new candidate; should bypass fresh and get new.
    provider_new = _FakeProvider("musicbrainz", [cand_new])
    result = await retrieve_and_hydrate(
        query, {"musicbrainz": provider_new}, session=db_session, config=config, refresh=True
    )
    assert any(c.ref.id == "mb-new" for c in result.candidates)
    # Now make refresh fail, should fallback to stale (old).
    provider_fail = _FakeProvider("musicbrainz", fail=True)
    result2 = await retrieve_and_hydrate(
        query, {"musicbrainz": provider_fail}, session=db_session, config=config, refresh=True
    )
    assert any(c.ref.id in ("mb-old", "mb-new") for c in result2.candidates)


def test_cache_version_mismatch_is_treated_as_miss(db_session: Session) -> None:
    key = query_hash("musicbrainz", "search_releases", "v-test")
    # Insert with old version.
    db_session.add(
        ProviderCache(
            provider="musicbrainz",
            operation="search_releases",
            query_hash=key,
            payload=[{"source": "musicbrainz", "ref": {"provider": "musicbrainz", "id": "old"}}],
            expires_at=datetime.now(UTC) + timedelta(days=1),
            schema_version=999,
        )
    )
    db_session.commit()
    assert cache_get(db_session, "musicbrainz", "search_releases", key) is None
    # Fresh with correct version should hit.
    cache_put(
        db_session,
        "musicbrainz",
        "search_releases",
        key,
        [{"source": "musicbrainz", "ref": {"provider": "musicbrainz", "id": "new"}}],
    )
    db_session.commit()
    assert cache_get(db_session, "musicbrainz", "search_releases", key) is not None


@pytest.mark.asyncio
async def test_stale_fallback_survives_retention_sweep_and_session_restart(tmp_path: Path) -> None:
    import subprocess
    import sys

    from muzilla.db.engine import create_db_engine, create_session_factory
    from muzilla.pipeline.retention import sweep_provider_cache

    db_path = tmp_path / "persist.db"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path.cwd(),
        env={"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
    )
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    query = ReleaseQuery(album="Persisted stale", album_artist="Artist")
    candidate = _make_candidate("musicbrainz", "mb-stale-persisted")
    provider_ok = _FakeProvider("musicbrainz", [candidate])
    with factory() as session:
        primed = await retrieve_and_hydrate(
            query, {"musicbrainz": provider_ok}, session=session, config=Config()
        )
        assert [item.ref.id for item in primed.candidates] == ["mb-stale-persisted"]
        rows = list(
            session.scalars(
                select(ProviderCache).where(ProviderCache.operation == "search_releases")
            )
        )
        assert len(rows) == 1
        rows[0].expires_at = datetime.now(UTC) - timedelta(days=1)
        session.flush()
        assert sweep_provider_cache(session) == 0
        session.commit()
    engine.dispose()

    engine2 = create_db_engine(db_path)
    factory2 = create_session_factory(engine2)
    with factory2() as session:
        offline_provider = _FakeProvider("musicbrainz", fail=True)
        offline = await retrieve_and_hydrate(
            query,
            {"musicbrainz": offline_provider},
            session=session,
            config=Config(providers_offline=True),
        )
        assert [item.ref.id for item in offline.candidates] == ["mb-stale-persisted"]
        assert offline_provider.search_calls == 0
        assert any("stale" in (outcome.detail or "") for outcome in offline.provider_outcomes)

        failed_provider = _FakeProvider("musicbrainz", fail=True)
        failed = await retrieve_and_hydrate(
            query, {"musicbrainz": failed_provider}, session=session, config=Config()
        )
        assert [item.ref.id for item in failed.candidates] == ["mb-stale-persisted"]
        assert failed_provider.search_calls == 1
        assert any("stale" in (outcome.detail or "") for outcome in failed.provider_outcomes)
    engine2.dispose()


@pytest.mark.asyncio
async def test_stale_hydration_provenance_survives_sweep_and_session_restart(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys

    from muzilla.db.engine import create_db_engine, create_session_factory
    from muzilla.pipeline.retention import sweep_provider_cache

    db_path = tmp_path / "stale-hydration.db"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path.cwd(),
        env={"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
    )
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    query = ReleaseQuery(album="Fresh search", album_artist="Artist")
    candidate = _make_candidate("musicbrainz", "mb-stale-hydration")
    provider_ok = _FakeProvider("musicbrainz", [candidate])
    with factory() as session:
        primed = await retrieve_and_hydrate(
            query, {"musicbrainz": provider_ok}, session=session, config=Config()
        )
        assert [item.ref.id for item in primed.candidates] == ["mb-stale-hydration"]
        rows = list(session.scalars(select(ProviderCache)))
        search_rows = [row for row in rows if row.operation == "search_releases"]
        hydration_rows = [row for row in rows if row.operation == "get_release"]
        assert len(search_rows) == len(hydration_rows) == 1
        search_rows[0].expires_at = datetime.now(UTC) + timedelta(days=1)
        hydration_rows[0].expires_at = datetime.now(UTC) - timedelta(days=1)
        session.flush()
        assert sweep_provider_cache(session) == 0
        session.commit()
    engine.dispose()

    restarted_engine = create_db_engine(db_path)
    restarted_factory = create_session_factory(restarted_engine)
    with restarted_factory() as session:
        provider_fail = _FakeProvider("musicbrainz", fail=True)
        result = await retrieve_and_hydrate(
            query, {"musicbrainz": provider_fail}, session=session, config=Config()
        )
        assert [item.ref.id for item in result.candidates] == ["mb-stale-hydration"]
        assert provider_fail.search_calls == 0
        assert provider_fail.hydrate_calls == 1
        outcome = next(
            outcome for outcome in result.provider_outcomes if outcome.provider == "musicbrainz"
        )
        assert outcome.status == "results"
        assert "stale" in (outcome.detail or "").lower()
        assert "hydration" in (outcome.detail or "").lower()
    restarted_engine.dispose()
