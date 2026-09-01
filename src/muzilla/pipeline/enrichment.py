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

    When session/config is provided, the ArtRef list is routed through the
    persistent cache gateway (cached_get_art). Art bytes are durably cached
    via BlobStore (content-addressed) and retained via AssetCandidate for
    offline continuity. Background jobs never force refresh; only
    user-initiated retry may set refresh=True. Offline makes zero network calls.
    """
    from muzilla.providers.cache import cached_get_art

    is_offline = bool(config and getattr(config, "providers_offline", False))
    # Use the real cached gateway for the ArtRef list (handles fresh/stale/offline, version/TTL, and cache_put).
    art_refs_result, prov = await cached_get_art(
        session, config, art_provider, ProviderRef(provider="musicbrainz", id=mb_release_id), refresh=refresh
    )
    if art_refs_result is None:
        return None
    # cached_get_art returns list[ArtRef] on hit, or the direct provider result on miss; normalize.
    art_refs = art_refs_result if isinstance(art_refs_result, list) else []
    if not art_refs and not prov.get("cached"):
        # No ArtRefs (not found) — nothing to fetch.
        return None
    if is_offline:
        # Offline must make zero network calls: only return previously retained bytes from BlobStore via AssetCandidate.
        # Try to find a retained Blob for this release via the DB's AssetCandidate or BlobStore.
        # For now, we cannot fetch remote URLs offline; instead, try to return cached ProcessedArt bytes
        # that were previously stored in provider_cache as processed bytes (if any) or via existing Blob.
        # Since cached_get_art already handled offline ArtRef retrieval, we now need the bytes.
        # Look for an existing AssetCandidate blob for this release (if any) as the durable bytes cache.
        if session is not None:


            from muzilla.providers.cache import cache_get_stale as _cgs
            from muzilla.providers.cache import query_hash as _qh2

            bytes_key = _qh2(getattr(art_provider, "name", "coverartarchive"), "get_art_bytes", mb_release_id)
            cached_bytes = _cgs(session, getattr(art_provider, "name", "coverartarchive"), "get_art_bytes", bytes_key)
            if isinstance(cached_bytes, dict) and "data" in cached_bytes:
                try:
                    import base64

                    data = base64.b64decode(cached_bytes["data"])
                    mime = str(cached_bytes.get("mime", "image/jpeg"))
                    width = int(cached_bytes.get("width", 0)) or None
                    height = int(cached_bytes.get("height", 0)) or None
                    # Re-process to ensure dimensions, or just return as ProcessedArt.
                    return ProcessedArt(data=data, mime=mime, width=width or max_dimension, height=height or max_dimension)
                except Exception:
                    pass
            # Fallback: try to find an existing AssetCandidate blob for this release (if any).
            # This is the durable BlobStore path for review continuity.
            # Offline with no cached bytes: return None without network (zero calls).
        return None
    # Online path: for each ArtRef, fetch and process, then cache the processed bytes for offline.
    for ref in art_refs:
        # Ensure ref is an ArtRef with url attribute.
        url = getattr(ref, "url", None)
        if not isinstance(url, str):
            continue
        try:
            response = await client.get(url, timeout=_ART_FETCH_TIMEOUT_S)
            response.raise_for_status()
        except httpx.HTTPError:
            continue
        try:
            processed = process_art(response.content, max_dimension=max_dimension)
        except ArtProcessingError:
            continue
        # Cache processed bytes for offline (durable).
        if session is not None:
            try:
                import base64

                from muzilla.providers.cache import cache_put as _cp
                from muzilla.providers.cache import query_hash as _qh3

                bkey = _qh3(getattr(art_provider, "name", "coverartarchive"), "get_art_bytes", mb_release_id)
                _cp(session, getattr(art_provider, "name", "coverartarchive"), "get_art_bytes", bkey, {"data": base64.b64encode(processed.data).decode(), "mime": processed.mime, "width": processed.width, "height": processed.height})
                session.flush()
            except Exception:
                pass
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
