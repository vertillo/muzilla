"""SQLAlchemy 2.0 declarative models.

Scoped incrementally per docs/PLAN.md's phase breakdown: Phase 0 seeds
only the schema_meta marker. Phase 1 adds tracks + track_groups — the
read-only catalog. Phase 2 adds change_sets/changes/apply_journal/blobs
— the staged-changes machinery, the product's spine (see docs/PLAN.md
§4-5). Phase 3 (this) adds provider_cache — the semantic cache over
normalized provider results. jobs/settings/users still land in
Phase 4+.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, UniqueConstraint
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


class ChangeSet(Base):
    """A proposed, reviewable, atomically-applicable unit of tag/grouping
    mutations (docs/PLAN.md §4). Nothing touches disk until a ChangeSet
    in DRAFT state is applied.

    State machine:
        DRAFT --edit/decide--> DRAFT
          |-- discard --> DISCARDED
          `-- apply --> APPLYING --+-- ok --> APPLIED --undo--> REVERTED
                                    +-- partial --> PARTIALLY_APPLIED
                                    `-- fail --> FAILED (compensated)

    Uses an integer primary key rather than the plan's UUID7 suggestion:
    SQLite has no native UUID type and this project has no cross-node
    id-generation requirement (single container, single writer) that
    UUID7 exists to solve elsewhere. Kept simple; revisit only if a
    real multi-writer need appears.
    """

    __tablename__ = "change_sets"
    __table_args__ = (Index("ix_change_sets_state", "state"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    source: Mapped[str]
    """manual_edit | match_proposal | rename | strip_tags |
    grouping_correction | undo_of:<id>. match_proposal/rename have no
    producer in Phase 2 (providers/paths land later) but the column
    accepts them so later phases need no migration."""
    source_ref: Mapped[dict[str, str]] = mapped_column(JSONDict, default=dict)
    """Free-form context about what produced this changeset (e.g. which
    grouping action, which strip-rule run)."""

    state: Mapped[str] = mapped_column(default="draft")
    """draft | applying | applied | partially_applied | failed |
    discarded | reverted"""

    scope_type: Mapped[str] = mapped_column(default="track")
    """track | group — what `scope_id` refers to."""
    scope_id: Mapped[int | None] = mapped_column(default=None)

    created_by: Mapped[str] = mapped_column(default="web")
    """cli | web | job"""
    job_id: Mapped[int | None] = mapped_column(default=None)

    stats: Mapped[dict[str, int]] = mapped_column(JSONDict, default=dict)
    """Summary counters (e.g. {"accepted": 3, "rejected": 1}) refreshed
    as changes are decided; cheap for list views to avoid a join+count."""

    candidate_source: Mapped[str | None] = mapped_column(default=None)
    """The single (provider, release) this changeset was staged from —
    nullable, since manual-edit/strip/grouping changesets have none.
    See docs/PLAN.md §3: one release, one source, no per-field merge."""
    candidate_ref: Mapped[str | None] = mapped_column(default=None)

    undo_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("change_sets.id", ondelete="SET NULL"), default=None
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    """Undo-retention horizon; journals/before_blobs may be pruned after
    this (docs/PLAN.md §4: 30 days / 500 changesets, whichever first)."""
    error: Mapped[str | None] = mapped_column(default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    changes: Mapped[list[Change]] = relationship(
        back_populates="change_set", cascade="all, delete-orphan", order_by="Change.seq"
    )


class Change(Base):
    """One field on one entity, staged inside a ChangeSet. Field-level
    granularity is non-negotiable (docs/PLAN.md §4): a user must be able
    to accept a title fix while rejecting a genre change in the same
    changeset.
    """

    __tablename__ = "changes"
    __table_args__ = (
        Index("ix_changes_change_set_id", "change_set_id"),
        Index("ix_changes_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    change_set_id: Mapped[int] = mapped_column(
        ForeignKey("change_sets.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(default=0)
    """Stable ordering within a changeset (display + apply order)."""

    entity_type: Mapped[str] = mapped_column(default="track")
    """track | group"""
    entity_id: Mapped[int] = mapped_column()

    field: Mapped[str]
    """Canonical field name from domain.fields, or a grouping-correction
    pseudo-field (e.g. 'group_id') for entity_type='group'."""
    op: Mapped[str] = mapped_column(default="set")
    """set | clear | strip | append | move | embed_art | write_lyrics"""

    old_value: Mapped[object | None] = mapped_column(JSON, default=None)
    """Arbitrary JSON — string, list of strings, number, or bool
    depending on the field's FieldType (domain/fields.py)."""
    new_value: Mapped[object | None] = mapped_column(JSON, default=None)
    old_blob_id: Mapped[int | None] = mapped_column(default=None)
    new_blob_id: Mapped[int | None] = mapped_column(default=None)

    confidence: Mapped[float | None] = mapped_column(default=None)
    severity: Mapped[str] = mapped_column(default="normal")
    """normal | destructive — clearing a populated field or moving a
    file is destructive; the UI requires an explicit toggle to bulk-
    accept those."""

    decision: Mapped[str] = mapped_column(default="pending")
    """pending | accepted | rejected"""
    apply_state: Mapped[str] = mapped_column(default="pending")
    """pending | applied | failed | conflicted"""

    is_manual: Mapped[bool] = mapped_column(default=False)
    """The user typed this value directly (in-review edit or manual
    editor), so it no longer matches the chosen candidate release."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    change_set: Mapped[ChangeSet] = relationship(back_populates="changes")


class ApplyJournal(Base):
    """Write-ahead journal for the apply path (docs/PLAN.md §4). Both
    the crash-recovery log and the source of before-state for undo.
    """

    __tablename__ = "apply_journal"
    __table_args__ = (Index("ix_apply_journal_change_set_id", "change_set_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    change_set_id: Mapped[int] = mapped_column(
        ForeignKey("change_sets.id", ondelete="CASCADE")
    )
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE")
    )
    path: Mapped[str]

    phase: Mapped[str] = mapped_column(default="tags")
    """tags | move | art"""
    state: Mapped[str] = mapped_column(default="pending")
    """pending | writing | done | failed | reverted"""

    before_hash: Mapped[str | None] = mapped_column(default=None)
    after_hash: Mapped[str | None] = mapped_column(default=None)
    before_blob: Mapped[dict[str, object]] = mapped_column(JSONDict, default=dict)
    """Complete original tag payload, small enough (<5KB) to inline as
    JSON; art bytes live in the blob store, referenced by id only."""
    before_path: Mapped[str | None] = mapped_column(default=None)
    after_path: Mapped[str | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class Blob(Base):
    """Content-addressed binary storage (art), stored on disk (sharded,
    not in SQLite — inline blobs would bloat the DB and wreck WAL
    checkpointing). Refcounted so a shared cover isn't stored once per
    track.
    """

    __tablename__ = "blobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(unique=True, index=True)
    mime: Mapped[str]
    size: Mapped[int]
    width: Mapped[int | None] = mapped_column(default=None)
    height: Mapped[int | None] = mapped_column(default=None)
    storage_path: Mapped[str]
    """Path relative to the blob store root, sharded by sha256 prefix."""
    refcount: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ProviderCache(Base):
    """Semantic cache of *normalized* provider results (docs/PLAN.md
    §2 — deliberately separate from the HTTP cache).

    Raw HTTP responses become useless the moment normalization code
    changes; caching the already-normalized `ReleaseCandidate` payload
    lets matching re-run offline (critical for tests and weight
    tuning) and survives provider-mapping bugfixes without a re-fetch.
    Keyed by `(provider, operation, query_hash)` so a release lookup
    and a search never collide even for the same provider.
    """

    __tablename__ = "provider_cache"
    __table_args__ = (
        UniqueConstraint("provider", "operation", "query_hash", name="uq_provider_cache_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(index=True)
    operation: Mapped[str]
    """search_releases | get_release | get_art | get_lyrics | fingerprint_lookup"""
    query_hash: Mapped[str]
    """blake2b of the normalized query/ref that produced this result."""
    payload: Mapped[dict[str, object] | list[object]] = mapped_column(JSON)
    """The normalized result — a list of ReleaseCandidate dicts for a
    search, a single dict for get_release, etc."""
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
