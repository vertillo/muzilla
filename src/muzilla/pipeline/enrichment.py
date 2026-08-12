"""Enrichment orchestration: ReplayGain, album art, and lyrics
(docs/product-spec.md — "complete, not just correct" metadata).

Same seam as `pipeline/matching.py`: pure computation (`audio/`,
`providers/`) meets the DB here, and the result is staged as an
ordinary `enrichment` ChangeSet via `changes/builder.py` — enrichment
never writes a file directly, exactly like every other mutation path
(CLAUDE.md: "nothing touches disk until a ChangeSet is applied").

Lives in `muzilla.pipeline` (not `services`) so `jobs/handlers/` can
call it directly without a layering violation — see pipeline/matching.py's
docstring for the same reasoning. `services.enrichment` re-exports this
module's public names for api/cli.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.audio.art import ArtProcessingError, ProcessedArt, process_art
from muzilla.audio.replaygain import compute_album_replaygain
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.db.models import ChangeSet, Track, TrackGroup
from muzilla.providers.base import ArtProvider, LyricsProvider, ProviderRef

_ART_FETCH_TIMEOUT_S = 30.0


def _draft_art_changeset(session: Session, group_id: int) -> ChangeSet | None:
    """Return the open proposal for this group's art without treating it as current art."""
    candidates = session.scalars(
        select(ChangeSet)
        .where(
            ChangeSet.source == "enrichment",
            ChangeSet.state == "draft",
            ChangeSet.scope_type == "group",
            ChangeSet.scope_id == group_id,
        )
        .order_by(ChangeSet.id.desc())
    )
    return next((change_set for change_set in candidates if change_set.source_ref.get("kind") == "art"), None)


def groups_needing_replaygain(session: Session) -> list[TrackGroup]:
    """Groups (album OR singleton — §7b treats them as equal peers)
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


def stage_replaygain_for_group(
    session: Session, group: TrackGroup, *, force: bool = False
) -> ChangeSet | None:
    """Analyzes every track in `group` together (album gain isn't
    decomposable per-file) and stages one `enrichment` ChangeSet
    covering all of them. Returns None if every track already has a
    value and `force` is False, or if rsgain produced no usable result
    at all (e.g. every file in the group is corrupt)."""
    tracks = [t for t in group.tracks if t.missing_since is None]
    if not force:
        tracks = [t for t in tracks if t.rg_track_gain is None]
    if not tracks:
        return None

    results = compute_album_replaygain([Path(t.path) for t in tracks])
    edits: dict[int, list[FieldEdit]] = {}
    for track in tracks:
        rg = results.get(Path(track.path))
        if rg is None:
            continue
        field_edits = [
            FieldEdit("rg_track_gain", rg.track_gain_db, op="set"),
            FieldEdit("rg_track_peak", rg.track_peak, op="set"),
        ]
        if rg.album_gain_db is not None:
            field_edits.append(FieldEdit("rg_album_gain", rg.album_gain_db, op="set"))
        if rg.album_peak is not None:
            field_edits.append(FieldEdit("rg_album_peak", rg.album_peak, op="set"))
        edits[track.id] = field_edits

    if not edits:
        return None

    return build_changeset(
        session,
        title=f"ReplayGain: {group.album or 'Untitled'}",
        source="enrichment",
        edits=edits,
        entity_type="track",
        source_ref={"kind": "replaygain"},
        scope_type="group",
        scope_id=group.id,
        created_by="job",
    )


def groups_needing_art(session: Session, *, prefer_existing: bool) -> list[TrackGroup]:
    """Groups with an MusicBrainz release id (CoverArtArchive's only
    lookup key — it has no search, see providers/coverartarchive.py)
    and no group-level art yet. When `prefer_existing` is True
    (config.enrichment.art_prefer_existing's default — docs/product-spec.md:
    "keep existing" since local art is often better than a provider's),
    a group where every track already has embedded art is excluded too."""
    stmt = select(TrackGroup).where(
        TrackGroup.mb_release_id.is_not(None), TrackGroup.art_blob_id.is_(None)
    )
    staged_group_ids = {
        change_set.scope_id
        for change_set in session.scalars(
            select(ChangeSet).where(
                ChangeSet.source == "enrichment",
                ChangeSet.state == "draft",
                ChangeSet.scope_type == "group",
            )
        )
        if change_set.scope_id is not None and change_set.source_ref.get("kind") == "art"
    }
    candidates = [
        group for group in session.scalars(stmt) if group.id not in staged_group_ids
    ]
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
) -> ProcessedArt | None:
    """Looks up CoverArtArchive art for a release, downloads the first
    ref, and resizes/re-encodes it. Returns None (never raises) when no
    art is found or every candidate fails to download/decode — the
    caller treats "no art available" as an ordinary, expected outcome,
    not an error to log loudly for every unmatched release."""
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


def stage_art_for_group(
    session: Session, group: TrackGroup, blob_store: BlobStore, data: bytes, mime: str
) -> ChangeSet | None:
    """Stores `data` in the blob store and stages an `embed_art` Change
    for every track in the group that lacks embedded art — group-level
    art (docs/product-spec.md `TrackGroup.art_blob_id`) is a single fetch
    applied to every track that needs it, not one fetch per track."""
    existing = _draft_art_changeset(session, group.id)
    if existing is not None:
        return existing

    tracks = [
        t for t in group.tracks if t.missing_since is None and not t.has_embedded_art
    ]
    if not tracks:
        return None

    blob = blob_store.put(session, data, mime=mime)
    session.flush()

    edits = {
        t.id: [FieldEdit(field="art", new_value=None, op="embed_art", new_blob_id=blob.id)]
        for t in tracks
    }
    change_set = build_changeset(
        session,
        title=f"Album art: {group.album or 'Untitled'}",
        source="enrichment",
        edits=edits,
        entity_type="track",
        source_ref={"kind": "art"},
        scope_type="group",
        scope_id=group.id,
        created_by="job",
    )
    session.flush()
    return change_set


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


async def stage_lyrics_for_track(
    session: Session, track: Track, provider: LyricsProvider
) -> ChangeSet | None:
    """Looks up LRCLIB for one track and stages a `write_lyrics` Change
    if found. Returns None (never raises) on no match — the caller
    treats that as an ordinary, expected outcome, not an error."""
    assert track.title is not None and track.artist is not None  # guaranteed by tracks_needing_lyrics
    result = await provider.get_lyrics(track.artist, track.title, track.duration_ms)
    if result is None:
        return None

    return build_changeset(
        session,
        title=f"Lyrics: {track.title}",
        source="enrichment",
        edits={
            track.id: [
                FieldEdit(
                    field="lyrics",
                    new_value={"text": result.text, "synced": result.synced},
                    op="write_lyrics",
                )
            ]
        },
        entity_type="track",
        source_ref={"kind": "lyrics"},
        scope_type="track",
        scope_id=track.id,
        created_by="job",
    )
