"""Pydantic response models for /api/tracks.

Mirrors `services.catalog.TrackSummary`/`TrackDetail` field-for-field;
kept as a separate schema (rather than `model_config` reusing the
dataclass) so the API's wire shape can evolve independently of the
internal service DTOs.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TrackSummaryOut(BaseModel):
    model_config = {"from_attributes": True}

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


class TrackDetailOut(TrackSummaryOut):
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


class TrackPageOut(BaseModel):
    items: list[TrackSummaryOut]
    next_cursor: str | None
    total: int


class FacetValueOut(BaseModel):
    value: str
    count: int


class TrackFacetsOut(BaseModel):
    artists: list[FacetValueOut]
    albums: list[FacetValueOut]
    genres: list[FacetValueOut]
    formats: list[FacetValueOut]
