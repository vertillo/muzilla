"""Enrichment orchestration: ReplayGain, album art, and lyrics.

Enrichment never writes a file directly; the reviewed ReviewBundle applier
owns file mutation. This module provides discovery helpers for groups/tracks
needing enrichment and is called from ReviewBundle composition in
``pipeline/proposals.py`` and ``jobs/handlers/*``.
"""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.audio.art import ArtProcessingError, ProcessedArt, process_art
from muzilla.db.models import Track, TrackGroup
from muzilla.providers.base import ArtProvider, ProviderRef

_ART_FETCH_TIMEOUT_S = 30.0


# _draft_art_changeset removed: enrichment art is now handled via ReviewBundle
# cover candidates (see pipeline/proposals.py and services/cover_assets.py).


def groups_needing_replaygain(session: Session) -> list[TrackGroup]:
    """Groups (album OR singleton — treated as equal peers)
    containing at least one track with no track-gain value yet. Scoped
    by group, not by track directly, because album gain is computed
    for a whole group's files together in one `rsgain` invocation —
    see `stage_replaygain_for_group`. `r128_track_gain` isn't checked:
    rsgain's `-O tab` mode always reports the EBU R128-based
    `Gain`/`Peak` pair together, so `rg_track_gain` being set is
    sufficient evidence a track was already analyzed."""
    group_ids = session.scalars(
        select(Track.group_id)
        .where(Track.missing_since.is_(None), Track.rg_track_gain.is_(None))
        .where(Track.group_id.is_not(None))
        .distinct()
    )
    ids = list(group_ids)
    if not ids:
        return []
    return list(session.scalars(select(TrackGroup).where(TrackGroup.id.in_(ids))))


def stage_replaygain_for_group(*_args: object, **_kwargs: object) -> None:
    """Legacy ChangeSet entry point removed; use ReviewBundle proposals."""
    raise NotImplementedError("enrichment ChangeSet staging removed: use ReviewBundle")


def groups_needing_art(session: Session, *, prefer_existing: bool) -> list[TrackGroup]:
    """Groups with an MusicBrainz release id and no group-level art yet."""
    stmt = select(TrackGroup).where(
        TrackGroup.mb_release_id.is_not(None), TrackGroup.art_blob_id.is_(None)
    )
    candidates = list(session.scalars(stmt))
    if not prefer_existing:
        return candidates
    return [
        g
        for g in candidates
        if not all(t.has_embedded_art for t in g.tracks if t.missing_since is None)
    ]


async def fetch_and_process_art(
    client: httpx.AsyncClient,
    art_provider: ArtProvider,
    mb_release_id: str,
    *,
    max_dimension: int,
    session: Session | None = None,
    config: object | None = None,
    refresh: bool = False,
) -> ProcessedArt | None:
    """Looks up art for a release, downloads the first ref, and resizes.

    When session/config is provided, the art lookup is routed through the
    persistent cache (versioned, TTL, stale fallback, offline). Background
    jobs never force refresh; only user-initiated retry may set refresh=True.
    Returns None (never raises) when no art is found or every candidate fails.
    Art bytes themselves are cached via BlobStore (content-addressed) and retained
    via AssetCandidate for review continuity; the provider's ArtRef list is also
    cached via provider_cache for gateway reuse.
    """
    from muzilla.providers.cache import cache_get_fresh, cache_get_stale, query_hash

    is_offline = bool(config and getattr(config, "providers_offline", False))
    cache_key = query_hash(art_provider.name if hasattr(art_provider, "name") else "coverartarchive", "get_art", mb_release_id) if session is not None else None
    # Try fresh cache unless refresh or offline.
    if session is not None and cache_key is not None and not refresh and not is_offline:
        fresh = cache_get_fresh(session, getattr(art_provider, "name", "coverartarchive"), "get_art", cache_key)
        if fresh is not None and isinstance(fresh, list):
            art_refs = []
            for item in fresh:
                if isinstance(item, dict) and "url" in item:
                    from muzilla.providers.base import ArtRef as _ArtRef

                    art_refs.append(_ArtRef(url=str(item["url"]), source=str(item.get("source", "")), width=item.get("width"), height=item.get("height"), mime=item.get("mime")))
            if art_refs:
                # Use cached ArtRefs to fetch and process.
                for ref in art_refs:
                    try:
                        response = await client.get(ref.url, timeout=_ART_FETCH_TIMEOUT_S)
                        response.raise_for_status()
                    except httpx.HTTPError:
                        continue
                    try:
                        processed = process_art(response.content, max_dimension=max_dimension)
                    except ArtProcessingError:
                        continue
                    return processed
    if is_offline and session is not None and cache_key is not None:
        stale = cache_get_stale(session, getattr(art_provider, "name", "coverartarchive"), "get_art", cache_key)
        if stale is not None and isinstance(stale, list):
            art_refs = []
            for item in stale:
                if isinstance(item, dict) and "url" in item:
                    from muzilla.providers.base import ArtRef as _ArtRef

                    art_refs.append(_ArtRef(url=str(item["url"]), source=str(item.get("source", "")), width=item.get("width"), height=item.get("height"), mime=item.get("mime")))
            for ref in art_refs:
                try:
                    response = await client.get(ref.url, timeout=_ART_FETCH_TIMEOUT_S)
                    response.raise_for_status()
                except httpx.HTTPError:
                    continue
                try:
                    processed = process_art(response.content, max_dimension=max_dimension)
                except ArtProcessingError:
                    continue
                return processed
            return None
    art_refs = await art_provider.get_art(ProviderRef(provider="musicbrainz", id=mb_release_id))
    for ref in art_refs:
        try:
            response = await client.get(ref.url, timeout=_ART_FETCH_TIMEOUT_S)
            response.raise_for_status()
        except httpx.HTTPError:
            continue
        try:
            processed = process_art(response.content, max_dimension=max_dimension)
        except ArtProcessingError:
            continue
        return processed
    return None


def stage_art_for_group(*_args: object, **_kwargs: object) -> None:
    """Legacy ChangeSet entry point removed; use ReviewBundle proposals."""
    raise NotImplementedError("enrichment ChangeSet staging removed: use ReviewBundle")


def tracks_needing_lyrics(session: Session) -> list[Track]:
    """Tracks with title+artist (LRCLIB's required query fields — see
    providers/lrclib.py) and no lyrics yet. Unlike art, lyrics is
    per-track, not per-group: even within one album, tracks obviously
    have different lyrics."""
    return list(
        session.scalars(
            select(Track).where(
                Track.missing_since.is_(None),
                Track.has_lyrics.is_(False),
                Track.title.is_not(None),
                Track.artist.is_not(None),
            )
        )
    )


async def stage_lyrics_for_track(*_args: object, **_kwargs: object) -> None:
    """Legacy ChangeSet entry point removed; use ReviewBundle proposals."""
    raise NotImplementedError("enrichment ChangeSet staging removed: use ReviewBundle")
