"""URL import cache: fresh, stale, offline, restart for manual_search._fetch_url_candidate."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import ProviderCache
from muzilla.providers.base import ProviderRef, ReleaseCandidate
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
    )  # type: ignore[arg-type]  # mock provider lacks full protocol
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
    )  # type: ignore[arg-type]  # mock
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
    )  # type: ignore[arg-type]  # mock
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
    )  # type: ignore[arg-type]  # mock
    result = await _fetch_url_candidate(ps_fail, recognized, session=db_session, config=config)
    assert result.ref.id == "mb-stale"
    assert mock_fail.get_release.call_count == 1  # tried network, then fell back
