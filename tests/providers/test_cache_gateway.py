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


def test_cache_persists_across_session_restart(tmp_path: Path) -> None:
    import subprocess
    import sys

    from muzilla.db.engine import create_db_engine, create_session_factory

    db_path = tmp_path / "persist.db"
    # Create DB and run migrations.

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path.cwd(),
        env={"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
    )
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    key = query_hash("musicbrainz", "search_releases", "persist")
    with factory() as s:
        cache_put(s, "musicbrainz", "search_releases", key, [{"a": 1}])
        s.commit()
    engine.dispose()
    # New engine/session should see the same entry.
    engine2 = create_db_engine(db_path)
    factory2 = create_session_factory(engine2)
    with factory2() as s2:
        assert cache_get(s2, "musicbrainz", "search_releases", key) == [{"a": 1}]
    engine2.dispose()
