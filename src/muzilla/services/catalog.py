"""Track catalog: browse/search/detail — the read-only surface `api`
and `cli` use to list and inspect tracks.

Returns plain dataclasses, never `db.models.Track` rows: `api`/`cli`
are forbidden from importing `muzilla.db` (see the import-linter
contracts), so this is also the boundary that keeps ORM objects from
leaking into request handlers or CLI commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.db.repo import tracks as tracks_repo


class CatalogSearchUnavailableError(RuntimeError):
    """A catalog text search could not be completed safely."""


@dataclass(frozen=True, slots=True)
class TrackSummary:
    id: int
    path: str
    filename: str
    ext: str
    title: str | None
    artist: str | None
    album: str | None
    album_artist: str | None
    track_no: int | None
    disc_no: int | None
    year: int | None
    genre: tuple[str, ...]
    duration_ms: int | None
    format: str | None
    bitrate: int | None
    has_embedded_art: bool
    has_lyrics: bool
    probe_error: str | None
    missing_since: datetime | None


@dataclass(frozen=True, slots=True)
class TrackDetail(TrackSummary):
    artists: tuple[str, ...]
    composer: str | None
    track_total: int | None
    disc_total: int | None
    original_year: int | None
    date: str | None
    compilation: bool
    label: str | None
    catalog_number: str | None
    barcode: str | None
    isrc: str | None
    country: str | None
    media: str | None
    mood: tuple[str, ...]
    bpm: int | None
    key: str | None
    mb_track_id: str | None
    mb_release_id: str | None
    mb_recording_id: str | None
    mb_artist_id: str | None
    discogs_release_id: str | None
    deezer_track_id: str | None
    acoustid_id: str | None
    sample_rate: int | None
    channels: int | None
    codec: str | None
    comment: str | None
    encoder: str | None
    extra_tags: dict[str, str]
    group_id: int | None
    grouping_needs_resolution: bool
    first_seen_at: datetime
    last_scanned_at: datetime
    lyrics_synced: bool
    rg_track_gain: float | None
    rg_album_gain: float | None
    art_blob_id: int | None


@dataclass(frozen=True, slots=True)
class TrackPage:
    items: list[TrackSummary]
    next_cursor: str | None
    total: int


@dataclass(frozen=True, slots=True)
class FacetValue:
    value: str
    count: int


@dataclass(frozen=True, slots=True)
class TrackFacets:
    artists: list[FacetValue]
    albums: list[FacetValue]
    genres: list[FacetValue]
    formats: list[FacetValue]


def _to_summary(t: Track) -> TrackSummary:
    return TrackSummary(
        id=t.id,
        path=t.path,
        filename=t.filename,
        ext=t.ext,
        title=t.title,
        artist=t.artist,
        album=t.album,
        album_artist=t.album_artist,
        track_no=t.track_no,
        disc_no=t.disc_no,
        year=t.year,
        genre=t.genre,
        duration_ms=t.duration_ms,
        format=t.format,
        bitrate=t.bitrate,
        has_embedded_art=t.has_embedded_art,
        has_lyrics=t.has_lyrics,
        probe_error=t.probe_error,
        missing_since=t.missing_since,
    )


def _to_detail(t: Track) -> TrackDetail:
    return TrackDetail(
        id=t.id,
        path=t.path,
        filename=t.filename,
        ext=t.ext,
        title=t.title,
        artist=t.artist,
        album=t.album,
        album_artist=t.album_artist,
        track_no=t.track_no,
        disc_no=t.disc_no,
        year=t.year,
        genre=t.genre,
        duration_ms=t.duration_ms,
        format=t.format,
        bitrate=t.bitrate,
        has_embedded_art=t.has_embedded_art,
        has_lyrics=t.has_lyrics,
        probe_error=t.probe_error,
        missing_since=t.missing_since,
        artists=t.artists,
        composer=t.composer,
        track_total=t.track_total,
        disc_total=t.disc_total,
        original_year=t.original_year,
        date=t.date,
        compilation=t.compilation,
        label=t.label,
        catalog_number=t.catalog_number,
        barcode=t.barcode,
        isrc=t.isrc,
        country=t.country,
        media=t.media,
        mood=t.mood,
        bpm=t.bpm,
        key=t.key,
        mb_track_id=t.mb_track_id,
        mb_release_id=t.mb_release_id,
        mb_recording_id=t.mb_recording_id,
        mb_artist_id=t.mb_artist_id,
        discogs_release_id=t.discogs_release_id,
        deezer_track_id=t.deezer_track_id,
        acoustid_id=t.acoustid_id,
        sample_rate=t.sample_rate,
        channels=t.channels,
        codec=t.codec,
        comment=t.comment,
        encoder=t.encoder,
        extra_tags=t.extra_tags,
        group_id=t.group_id,
        grouping_needs_resolution=bool(
            t.group is not None
            and not t.group.is_pinned
            and t.group.grouping_confidence is not None
            and t.group.grouping_confidence < 0.8
        ),
        first_seen_at=t.first_seen_at,
        last_scanned_at=t.last_scanned_at,
        lyrics_synced=t.lyrics_synced,
        rg_track_gain=t.rg_track_gain,
        rg_album_gain=t.rg_album_gain,
        art_blob_id=t.art_blob_id,
    )


def browse_tracks(
    session: Session,
    *,
    q: str | None = None,
    sort: str = "title",
    direction: str = "asc",
    cursor: str | None = None,
    limit: int = 100,
    artist: str | None = None,
    album: str | None = None,
    genre: str | None = None,
    format: str | None = None,
    flags: tuple[str, ...] = (),
) -> TrackPage:
    try:
        page = tracks_repo.list_tracks(
            session,
            q=q,
            sort=sort,
            direction=direction,
            cursor=cursor,
            limit=limit,
            artist=artist,
            album=album,
            genre=genre,
            format=format,
            flags=flags,
        )
    except SQLAlchemyError as exc:
        if q is None or not q.strip():
            raise
        raise CatalogSearchUnavailableError("catalog search is temporarily unavailable") from exc
    return TrackPage(
        items=[_to_summary(t) for t in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


def get_track_detail(session: Session, track_id: int) -> TrackDetail | None:
    track = tracks_repo.get_track(session, track_id)
    return _to_detail(track) if track is not None else None


def get_track_facets(session: Session, *, q: str | None = None) -> TrackFacets:
    """Distinct artist/album/genre/format values (search-scoped, not
    filter-scoped — facet options remain discoverable across active filters),
    for populating catalog filter dropdowns from the full
    table rather than whatever page(s) the client has fetched."""
    try:
        facets = tracks_repo.get_facets(session, q=q)
    except SQLAlchemyError as exc:
        if q is None or not q.strip():
            raise
        raise CatalogSearchUnavailableError("catalog search is temporarily unavailable") from exc
    return TrackFacets(
        artists=[FacetValue(f.value, f.count) for f in facets.artists],
        albums=[FacetValue(f.value, f.count) for f in facets.albums],
        genres=[FacetValue(f.value, f.count) for f in facets.genres],
        formats=[FacetValue(f.value, f.count) for f in facets.formats],
    )
