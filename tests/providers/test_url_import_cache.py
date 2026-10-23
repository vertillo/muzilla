"""URL import cache: fresh, stale, offline, restart for manual_search._fetch_url_candidate."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import ProviderCache
from muzilla.providers.base import ArtRef, CandidateTrack, ProviderRef, ReleaseCandidate
from muzilla.providers.cache import cache_put, cached_get_release, cached_get_track, query_hash
from muzilla.providers.set import ProviderSet
from muzilla.providers.url_registry import CandidateUrlRef
from muzilla.services.manual_search import _fetch_url_candidate


def _make_candidate(provider: str, pid: str) -> ReleaseCandidate:
    return ReleaseCandidate(
        source=provider,
        ref=ProviderRef(provider=provider, id=pid),
        album="Album",
        album_artist="Artist",
        tracks=(),
    )


@pytest.mark.asyncio
async def test_cached_get_track_round_trips_candidate_and_provenance(
    db_session: Session, migrated_db: Path
) -> None:
    request_ref = ProviderRef(provider="deezer", id="track-7")
    representative = CandidateTrack(
        position=2,
        title="Requested Track",
        artist="Guest Artist",
        duration_ms=123_456,
        disc_number=1,
        isrc="USAAA2400001",
        mb_track_id="mb-track-7",
        mb_recording_id="mb-recording-7",
    )
    candidate = replace(
        _make_candidate("deezer", "album-7"),
        candidate_type="track",
        tracks=(CandidateTrack(position=1, title="Intro"), representative),
        representative_track=representative,
        deezer_album_id="album-7",
        external_ids={"deezer": "album-7"},
        art_refs=(ArtRef(url="https://example.test/cover.jpg", source="deezer", width=500),),
        raw={"track_id": "track-7", "artists": ["Guest Artist"]},
    )
    provider = AsyncMock()
    provider.name = "deezer"
    provider.get_track_candidate = AsyncMock(return_value=candidate)
    config = Config()

    cold, cold_provenance = await cached_get_track(db_session, config, provider, request_ref)
    assert cold == candidate
    assert isinstance(cold, ReleaseCandidate)
    assert cold_provenance == {"cached": False, "stale": False, "offline": False}

    hot, hot_provenance = await cached_get_track(db_session, config, provider, request_ref)
    assert hot == candidate
    assert isinstance(hot, ReleaseCandidate)
    assert hot_provenance == {"cached": True, "stale": False, "offline": False}
    assert provider.get_track_candidate.await_count == 1
    db_session.commit()

    engine = create_db_engine(migrated_db)
    try:
        factory = create_session_factory(engine)
        with factory() as restarted:
            after_restart, restart_provenance = await cached_get_track(
                restarted, config, provider, request_ref
            )
            assert after_restart == candidate
            assert isinstance(after_restart, ReleaseCandidate)
            assert restart_provenance == {"cached": True, "stale": False, "offline": False}
            assert provider.get_track_candidate.await_count == 1

            key = query_hash("deezer", "get_track", request_ref.id)
            row = restarted.scalar(
                select(ProviderCache).where(
                    ProviderCache.provider == "deezer",
                    ProviderCache.operation == "get_track",
                    ProviderCache.query_hash == key,
                )
            )
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            restarted.commit()

            offline, offline_provenance = await cached_get_track(
                restarted, Config(providers_offline=True), provider, request_ref
            )
            assert offline == candidate
            assert isinstance(offline, ReleaseCandidate)
            assert offline_provenance == {"cached": True, "stale": True, "offline": True}
            assert provider.get_track_candidate.await_count == 1

            provider.get_track_candidate.side_effect = RuntimeError("provider failure")
            stale, stale_provenance = await cached_get_track(
                restarted, config, provider, request_ref
            )
            assert stale == candidate
            assert isinstance(stale, ReleaseCandidate)
            assert stale_provenance == {"cached": True, "stale": True, "offline": False}
            assert provider.get_track_candidate.await_count == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("operation", "method_name", "request_ref", "payload"),
    [
        pytest.param(
            "get_release",
            "get_release",
            ProviderRef(provider="deezer", id="release-7"),
            {"source": "deezer"},
            id="release-missing-ref",
        ),
        pytest.param(
            "get_release",
            "get_release",
            ProviderRef(provider="deezer", id="release-7"),
            {"source": "deezer", "ref": {"provider": "deezer", "id": 7}},
            id="release-invalid-ref",
        ),
        pytest.param(
            "get_release",
            "get_release",
            ProviderRef(provider="deezer", id="release-7"),
            {
                "source": "deezer",
                "ref": {"provider": "deezer", "id": "release-7"},
                "album": {},
            },
            id="release-invalid-album",
        ),
        pytest.param(
            "get_track",
            "get_track_candidate",
            ProviderRef(provider="deezer", id="track-7"),
            {
                "source": "deezer",
                "ref": {"provider": "deezer", "id": "release-7"},
                "tracks": [{"position": 1, "title": "Track", "duration_ms": {}}],
            },
            id="track-invalid-duration",
        ),
    ],
)
@pytest.mark.parametrize("scenario", ["online", "offline", "provider-error"])
@pytest.mark.asyncio
async def test_invalid_cached_candidate_fails_closed_for_both_gateways(
    db_session: Session,
    operation: str,
    method_name: str,
    request_ref: ProviderRef,
    payload: object,
    scenario: str,
) -> None:
    candidate = _make_candidate("deezer", "release-7")
    if operation == "get_track":
        candidate = replace(candidate, candidate_type="track")
    provider = AsyncMock()
    provider.name = "deezer"
    network_call = AsyncMock(return_value=candidate)
    setattr(provider, method_name, network_call)
    key = query_hash("deezer", operation, request_ref.id)
    cache_put(db_session, "deezer", operation, key, payload)

    if scenario != "online":
        row = db_session.scalar(
            select(ProviderCache).where(
                ProviderCache.provider == "deezer",
                ProviderCache.operation == operation,
                ProviderCache.query_hash == key,
            )
        )
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    if scenario == "provider-error":
        network_call.side_effect = RuntimeError("provider failure")

    gateway = cached_get_release if operation == "get_release" else cached_get_track
    config = Config(providers_offline=scenario == "offline")
    if scenario == "provider-error":
        with pytest.raises(RuntimeError, match="provider failure"):
            await gateway(db_session, config, provider, request_ref)
        assert network_call.await_count == 1
    elif scenario == "offline":
        result, provenance = await gateway(db_session, config, provider, request_ref)
        assert result is None
        assert provenance == {"cached": False, "stale": False, "offline": True}
        assert network_call.await_count == 0
    else:
        result, provenance = await gateway(db_session, config, provider, request_ref)
        assert result == candidate
        assert isinstance(result, ReleaseCandidate)
        assert provenance == {"cached": False, "stale": False, "offline": False}
        assert network_call.await_count == 1


@pytest.mark.asyncio
async def test_url_import_uses_cache_fresh_and_reuses(db_session: Session) -> None:
    cand = _make_candidate("musicbrainz", "mb-url-1")
    mock_provider = AsyncMock()
    mock_provider.name = "musicbrainz"
    mock_provider.capabilities = {
        __import__("muzilla.providers.base", fromlist=["Capability"]).Capability.GET_RELEASE
    }
    mock_provider.get_release = AsyncMock(return_value=cand)
    # Need to make it look like a MetadataProvider with get_release
    from muzilla.providers.base import Capability

    mock_provider.capabilities = frozenset({Capability.GET_RELEASE})
    # Mock provider_set with just musicbrainz
    ps = ProviderSet(
        metadata={"musicbrainz": mock_provider}, art={}, lyrics={}, fingerprint={}, clients=()
    )
    recognized = CandidateUrlRef(
        provider="musicbrainz", candidate_type="release", provider_id="mb-url-1"
    )
    config = Config()
    # First call: network
    result1 = await _fetch_url_candidate(ps, recognized, session=db_session, config=config)
    assert result1.ref.id == "mb-url-1"
    assert mock_provider.get_release.call_count == 1
    # Second call: should hit fresh cache, no additional network
    mock_provider.get_release.reset_mock()
    result2 = await _fetch_url_candidate(ps, recognized, session=db_session, config=config)
    assert result2.ref.id == "mb-url-1"
    assert mock_provider.get_release.call_count == 0


@pytest.mark.asyncio
async def test_url_import_offline_returns_only_cached(db_session: Session) -> None:
    cand = _make_candidate("musicbrainz", "mb-offline")
    mock_provider = AsyncMock()
    mock_provider.name = "musicbrainz"
    from muzilla.providers.base import Capability

    mock_provider.capabilities = frozenset({Capability.GET_RELEASE})
    mock_provider.get_release = AsyncMock(return_value=cand)
    ps = ProviderSet(
        metadata={"musicbrainz": mock_provider}, art={}, lyrics={}, fingerprint={}, clients=()
    )
    recognized = CandidateUrlRef(
        provider="musicbrainz", candidate_type="release", provider_id="mb-offline"
    )
    config_offline = Config(providers_offline=True)
    # No prior cache, offline should return not_found (via fetch error) -> but our gateway returns None for offline with no cache
    # So we expect UrlCandidateFetchError not_found
    from muzilla.services.manual_search import UrlCandidateFetchError

    with pytest.raises(UrlCandidateFetchError):
        await _fetch_url_candidate(ps, recognized, session=db_session, config=config_offline)
    assert mock_provider.get_release.call_count == 0
    # Now prime cache online, then offline should return cached
    config_online = Config()
    mock_provider.get_release = AsyncMock(return_value=cand)
    await _fetch_url_candidate(ps, recognized, session=db_session, config=config_online)
    mock_provider.get_release.reset_mock()
    result_offline = await _fetch_url_candidate(
        ps, recognized, session=db_session, config=config_offline
    )
    assert result_offline.ref.id == "mb-offline"
    assert mock_provider.get_release.call_count == 0


@pytest.mark.asyncio
async def test_url_import_stale_fallback_on_failure(db_session: Session) -> None:
    cand = _make_candidate("musicbrainz", "mb-stale")
    mock_ok = AsyncMock()
    mock_ok.name = "musicbrainz"
    from muzilla.providers.base import Capability

    mock_ok.capabilities = frozenset({Capability.GET_RELEASE})
    mock_ok.get_release = AsyncMock(return_value=cand)
    ps_ok = ProviderSet(
        metadata={"musicbrainz": mock_ok}, art={}, lyrics={}, fingerprint={}, clients=()
    )
    recognized = CandidateUrlRef(
        provider="musicbrainz", candidate_type="release", provider_id="mb-stale"
    )
    config = Config()
    await _fetch_url_candidate(ps_ok, recognized, session=db_session, config=config)
    # Now make provider fail, expire cache to stale, should fallback
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    row = db_session.execute(select(ProviderCache)).scalar_one()
    row.expires_at = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()
    mock_fail = AsyncMock()
    mock_fail.name = "musicbrainz"
    mock_fail.capabilities = frozenset({Capability.GET_RELEASE})
    mock_fail.get_release = AsyncMock(side_effect=RuntimeError("provider failure"))
    ps_fail = ProviderSet(
        metadata={"musicbrainz": mock_fail}, art={}, lyrics={}, fingerprint={}, clients=()
    )
    result = await _fetch_url_candidate(ps_fail, recognized, session=db_session, config=config)
    assert result.ref.id == "mb-stale"
    assert mock_fail.get_release.call_count == 1  # tried network, then fell back
