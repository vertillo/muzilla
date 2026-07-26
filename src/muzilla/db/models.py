"""SQLAlchemy 2.0 declarative models.

Scoped incrementally per docs/PLAN.md's phase breakdown: Phase 0 seeds
only the schema_meta marker. Phase 1 (this) adds tracks + track_groups
— the read-only catalog. change_sets/changes/apply_journal/blobs/jobs
land in Phase 2+ once the changeset machinery is built.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from muzilla.db.types import JSONDict, JSONList


class Base(DeclarativeBase):
    pass


class SchemaMeta(Base):
    """Single-row marker table confirming migrations have run."""

    __tablename__ = "schema_meta"

    id: Mapped[int] = mapped_column(primary_key=True)
    initialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class TrackGroup(Base):
    """A derived, correctable grouping of tracks — an album OR a singleton.

    Not a user-facing authority the way beets' `albums` table is: in a
    flat, mixed library there is no directory signal, so grouping is
    always inferred and always subject to correction. `kind` covers
    both albums and loose singles as equal peers rather than treating
    singletons as an afterthought (see docs/PLAN.md §7b).

    No dir_path column: it was a grouping input in an earlier draft
    and was deliberately removed, since a flat library has one
    directory for everything and it would silently collapse the whole
    catalog into a single group.
    """

    __tablename__ = "track_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True, index=True)

    kind: Mapped[str] = mapped_column(default="unknown")
    """album | singleton | partial_album | unknown"""

    grouping_basis: Mapped[str | None] = mapped_column(default=None)
    """release_id | barcode | fingerprint | tags | singleton | manual"""

    grouping_confidence: Mapped[float | None] = mapped_column(default=None)
    is_pinned: Mapped[bool] = mapped_column(default=False)
    """User corrected this grouping; rescans must never re-guess it."""

    album: Mapped[str | None] = mapped_column(default=None, index=True)
    album_artist: Mapped[str | None] = mapped_column(default=None, index=True)
    year: Mapped[int | None] = mapped_column(default=None)
    original_year: Mapped[int | None] = mapped_column(default=None)
    track_count: Mapped[int] = mapped_column(default=0)
    disc_count: Mapped[int] = mapped_column(default=1)
    label: Mapped[str | None] = mapped_column(default=None)
    catalog_number: Mapped[str | None] = mapped_column(default=None)
    barcode: Mapped[str | None] = mapped_column(default=None)

    expected_track_count: Mapped[int | None] = mapped_column(default=None)
    """From the matched release, drives 'N of M tracks present' indicators."""

    mb_release_id: Mapped[str | None] = mapped_column(default=None, index=True)
    discogs_release_id: Mapped[str | None] = mapped_column(default=None)
    art_blob_id: Mapped[int | None] = mapped_column(default=None)
    is_compilation: Mapped[bool] = mapped_column(default=False)

    match_state: Mapped[str] = mapped_column(default="unmatched")
    """unmatched | proposed | matched | manual"""
    match_distance: Mapped[float | None] = mapped_column(default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    tracks: Mapped[list[Track]] = relationship(back_populates="group")


class Track(Base):
    __tablename__ = "tracks"
    __table_args__ = (
        UniqueConstraint("path", name="uq_tracks_path"),
        Index("ix_tracks_group_id", "group_id"),
        Index("ix_tracks_mb_release_id", "mb_release_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Filesystem identity
    path: Mapped[str] = mapped_column(unique=True)
    """Absolute, NFC-normalized path. Source of truth — the DB is an
    index over the filesystem, not the other way around."""
    filename: Mapped[str]
    ext: Mapped[str]
    size_bytes: Mapped[int]
    mtime_ns: Mapped[int]
    content_hash: Mapped[str | None] = mapped_column(default=None)
    """blake2b(first_64KB + last_64KB + size) — cheap partial hash, not
    a full-file hash (ruinous on large libraries). Used as a rescan
    fast-path alongside (size, mtime_ns)."""
    tag_hash: Mapped[str | None] = mapped_column(default=None, index=True)
    """blake2b of the canonical tag serialization. The real drift-
    detection check once writes exist (Phase 2): if this changes
    between staging and applying a change, the file was edited by
    something else and the write must be treated as a conflict."""

    # Technical (read-only, from probe)
    format: Mapped[str | None] = mapped_column(default=None)
    duration_ms: Mapped[int | None] = mapped_column(default=None)
    bitrate: Mapped[int | None] = mapped_column(default=None)
    sample_rate: Mapped[int | None] = mapped_column(default=None)
    channels: Mapped[int | None] = mapped_column(default=None)
    codec: Mapped[str | None] = mapped_column(default=None)

    # Canonical metadata (mirrors domain.metadata.TrackMeta / domain.fields)
    title: Mapped[str | None] = mapped_column(default=None)
    artist: Mapped[str | None] = mapped_column(default=None)
    artists: Mapped[tuple[str, ...]] = mapped_column(JSONList, default=())
    album: Mapped[str | None] = mapped_column(default=None)
    album_artist: Mapped[str | None] = mapped_column(default=None)
    composer: Mapped[str | None] = mapped_column(default=None)
    track_no: Mapped[int | None] = mapped_column(default=None)
    track_total: Mapped[int | None] = mapped_column(default=None)
    disc_no: Mapped[int | None] = mapped_column(default=None)
    disc_total: Mapped[int | None] = mapped_column(default=None)
    year: Mapped[int | None] = mapped_column(default=None)
    original_year: Mapped[int | None] = mapped_column(default=None)
    date: Mapped[str | None] = mapped_column(default=None)
    compilation: Mapped[bool] = mapped_column(default=False)

    label: Mapped[str | None] = mapped_column(default=None)
    catalog_number: Mapped[str | None] = mapped_column(default=None)
    barcode: Mapped[str | None] = mapped_column(default=None)
    isrc: Mapped[str | None] = mapped_column(default=None)
    country: Mapped[str | None] = mapped_column(default=None)
    media: Mapped[str | None] = mapped_column(default=None)

    genre: Mapped[tuple[str, ...]] = mapped_column(JSONList, default=())
    mood: Mapped[tuple[str, ...]] = mapped_column(JSONList, default=())
    bpm: Mapped[int | None] = mapped_column(default=None)
    key: Mapped[str | None] = mapped_column(default=None)

    mb_track_id: Mapped[str | None] = mapped_column(default=None)
    mb_release_id: Mapped[str | None] = mapped_column(default=None)
    mb_recording_id: Mapped[str | None] = mapped_column(default=None)
    mb_artist_id: Mapped[str | None] = mapped_column(default=None)
    discogs_release_id: Mapped[str | None] = mapped_column(default=None)
    deezer_track_id: Mapped[str | None] = mapped_column(default=None)
    acoustid_id: Mapped[str | None] = mapped_column(default=None)
    acoustid_fingerprint: Mapped[str | None] = mapped_column(default=None)

    rg_track_gain: Mapped[float | None] = mapped_column(default=None)
    rg_track_peak: Mapped[float | None] = mapped_column(default=None)
    rg_album_gain: Mapped[float | None] = mapped_column(default=None)
    rg_album_peak: Mapped[float | None] = mapped_column(default=None)
    r128_track_gain: Mapped[float | None] = mapped_column(default=None)

    comment: Mapped[str | None] = mapped_column(default=None)
    encoder: Mapped[str | None] = mapped_column(default=None)

    has_lyrics: Mapped[bool] = mapped_column(default=False)
    lyrics_synced: Mapped[bool] = mapped_column(default=False)
    has_embedded_art: Mapped[bool] = mapped_column(default=False)
    art_blob_id: Mapped[int | None] = mapped_column(default=None)

    extra_tags: Mapped[dict[str, str]] = mapped_column(JSONDict, default=dict)
    """Long-tail raw tag frames not mapped to a canonical field."""

    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("track_groups.id", ondelete="SET NULL"), default=None
    )

    probe_error: Mapped[str | None] = mapped_column(default=None)
    """Set when the file exists but tags/probe failed to read — the
    scan continues past it rather than aborting."""

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    last_scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    last_written_at: Mapped[datetime | None] = mapped_column(default=None)
    missing_since: Mapped[datetime | None] = mapped_column(default=None)
    """Set when a rescan no longer finds this path; the row is kept
    (not deleted) so history/undo remain possible."""

    group: Mapped[TrackGroup | None] = relationship(back_populates="tracks")
