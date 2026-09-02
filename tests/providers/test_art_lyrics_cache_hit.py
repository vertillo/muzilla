"""Fresh cache-hit for art and lyrics must return objects with no provider call."""

# mypy: ignore-errors

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.domain.metadata import LyricsResult
from muzilla.providers.base import ArtRef, ProviderRef
from muzilla.providers.cache import cached_get_art, cached_get_lyrics
from muzilla.providers.set import ProviderSet


@pytest.mark.asyncio
async def test_cached_lyrics_fresh_hit_returns_LyricsResult_with_no_provider_call(db_session: Session):
    # Prime cache via first call (provider succeeds)
    mock_provider = AsyncMock()
    mock_provider.name = "lrclib"
    mock_provider.get_lyrics = AsyncMock(return_value=LyricsResult(text="hello", synced=False, source="lrclib"))
    config = Config()
    result1, prov1 = await cached_get_lyrics(db_session, config, mock_provider, "Artist", "Title", None)
    assert result1 is not None
    assert result1.text == "hello"  # type: ignore[attr-defined]
    assert prov1["cached"] is False
    db_session.commit()
    # Second call: fresh cache hit should return LyricsResult without calling provider
    mock_provider.get_lyrics.reset_mock()
    mock_provider.get_lyrics.side_effect = AssertionError("provider should not be called on fresh hit")
    result2, prov2 = await cached_get_lyrics(db_session, config, mock_provider, "Artist", "Title", None)
    assert result2 is not None
    assert isinstance(result2, LyricsResult)
    assert result2.text == "hello"
    assert result2.synced is False
    assert result2.source == "lrclib"
    assert prov2["cached"] is True
    assert prov2["stale"] is False
    mock_provider.get_lyrics.assert_not_called()


@pytest.mark.asyncio
async def test_cached_art_fresh_hit_returns_ArtRef_with_no_provider_call(db_session: Session):
    mock_provider = AsyncMock()
    mock_provider.name = "deezer"
    art_ref = ArtRef(url="http://example.com/cover.jpg", source="deezer", width=500, height=500, mime="image/jpeg")
    mock_provider.get_art = AsyncMock(return_value=[art_ref])
    config = Config()
    ref = ProviderRef(provider="deezer", id="123")
    result1, prov1 = await cached_get_art(db_session, config, mock_provider, ref)
    assert result1 is not None
    assert len(result1) == 1  # type: ignore[arg-type]
    assert prov1["cached"] is False
    db_session.commit()
    # Fresh hit
    mock_provider.get_art.reset_mock()
    mock_provider.get_art.side_effect = AssertionError("should not call provider on fresh hit")
    result2, prov2 = await cached_get_art(db_session, config, mock_provider, ref)
    assert result2 is not None
    assert len(result2) == 1  # type: ignore[arg-type]
    assert isinstance(result2[0], ArtRef)  # type: ignore[index]
    assert result2[0].url == "http://example.com/cover.jpg"  # type: ignore[index,union-attr]
    assert prov2["cached"] is True
    mock_provider.get_art.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_lyrics_handler_uses_cache_on_fresh_hit(db_session: Session, tmp_path):
    # End-to-end via handler with fresh cache and provider forbidden

    # Setup track needing lyrics
    from datetime import UTC, datetime

    from muzilla.db.models import Track
    from muzilla.jobs.handlers.enrich_lyrics import handle_enrich_lyrics
    from muzilla.providers.base import Capability

    now = datetime.now(UTC)
    track = Track(
        path=str(tmp_path / "t.mp3"),
        filename="t.mp3",
        ext=".mp3",
        size_bytes=1,
        mtime_ns=1,
        title="Title",
        artist="Artist",
        first_seen_at=now,
        last_scanned_at=now,
        has_lyrics=False,
    )
    (tmp_path / "t.mp3").write_bytes(b"dummy")
    db_session.add(track)
    db_session.flush()
    # Create a review bundle for the track (needed by handler)
    from muzilla.domain.reviews import BundleState
    from muzilla.pipeline.reviews import put_revision, transition_bundle

    write = put_revision(
        db_session,
        logical_key=f"track:{track.id}",
        title="Review",
        scope_type="track",
        scope_id=track.id,
        source_snapshot={"items": [{"source_type": "track", "source_id": track.id, "path": track.path}]},
        operations=(),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    db_session.commit()
    # Prime cache
    mock_provider = AsyncMock()
    mock_provider.name = "lrclib"
    mock_provider.capabilities = frozenset({Capability.LYRICS})
    mock_provider.get_lyrics = AsyncMock(return_value=LyricsResult(text="cached lyrics", synced=True, source="lrclib"))

    ps = ProviderSet(metadata={}, art={}, lyrics={"lrclib": mock_provider}, fingerprint={}, clients=())  # type: ignore[arg-type]
    from muzilla.config.schema import Config as _Cfg
    from muzilla.jobs.registry import WorkerContext

    config = _Cfg()
    ctx = WorkerContext(config=config, provider_set=ps)  # type: ignore[arg-type]
    # First, prime via direct cached call
    from muzilla.providers.cache import cached_get_lyrics as _cgl

    await _cgl(db_session, config, mock_provider, "Artist", "Title", None)
    db_session.commit()
    # Now make provider fail, but cache should still serve via handler
    mock_provider.get_lyrics.reset_mock()
    mock_provider.get_lyrics.side_effect = AssertionError("should not be called, cache hit")
    from muzilla.db.models import Job

    job = Job(type="enrich_lyrics", payload={"track_ids": [track.id]})
    db_session.add(job)
    db_session.commit()
    # Mock progress
    class _P:
        def log(self, *a, **kw): pass
        def update(self, *a, **kw): pass

    result = await handle_enrich_lyrics(db_session, job, _P(), ctx)  # type: ignore[arg-type]
    assert result["found"] == 1
    mock_provider.get_lyrics.assert_not_called()
