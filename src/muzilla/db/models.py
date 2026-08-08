"""SQLAlchemy 2.0 declarative models.

Scoped incrementally per docs/PLAN.md's phase breakdown: Phase 0 seeds
only the schema_meta marker. Phase 1 adds tracks + track_groups — the
read-only catalog. Phase 2 adds change_sets/changes/apply_journal/blobs
— the staged-changes machinery, the product's spine (see docs/PLAN.md
§4-5). Phase 3 adds provider_cache — the semantic cache over
normalized provider results. Phase 4 (this) adds jobs/job_events (the
background worker's queue + SSE replay log) and
import_sessions/import_tasks (the resumable scan→fingerprint→group→
match orchestration). settings/users still land whenever DB-backed
config/auth actually needs them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from muzilla.db.types import JSONDict, JSONList


class Base(DeclarativeBase):
    pass


class SchemaMeta(Base):
    """Single-row marker table confirming migrations have run.

    Also carries auth_epoch (docs/PLAN.md §12c): bumped on logout so a
    session cookie signed before that point is rejected even though its
    HMAC signature is still valid — logout otherwise only deletes the
    client-side cookie, so a copy captured earlier stays valid for the
    full 30-day session TTL.
    """

    __tablename__ = "schema_meta"

    id: Mapped[int] = mapped_column(primary_key=True)
    initialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    auth_epoch: Mapped[int] = mapped_column(default=0, server_default="0")


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
    discarded | reverted | undo_expired

    undo_expired (docs/PLAN.md §11c) is reached only from applied or
    partially_applied, when the retention sweep prunes every ApplyJournal
    row for this changeset past the age/count threshold — undo requires
    those rows (build_undo_changeset reverses Changes using
    apply_state="applied", but the *journal* is what crash-recovery and
    the applied-vs-partially_applied distinction rely on being fresh).
    changes/undo.py checks state in ("applied", "partially_applied")
    before building an undo changeset, so undo_expired blocks it there;
    the frontend's ChangesList hides the Undo button on the same check."""

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

    import_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_sessions.id", ondelete="SET NULL"), default=None
    )
    """Set when a match job produced this changeset during an import —
    lets the review inbox (GET /api/imports/{id}) filter changesets by
    a plain indexed FK instead of scanning source_ref JSON."""

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


class ReviewBundle(Base):
    """Stable inbox identity for one logical review scope.

    Legacy ChangeSets remain alongside this foundation during the one-way migration;
    new producers will move to bundles one at a time without dual-writing either model.
    """

    __tablename__ = "review_bundles"
    __table_args__ = (
        CheckConstraint(
            "state IN ('preparing', 'ready', 'needs_attention', 'applying', "
            "'applied', 'partially_applied', 'failed', 'discarded')",
            name="ck_review_bundles_state",
        ),
        Index("ix_review_bundles_state", "state"),
        Index(
            "uq_review_bundles_active_logical_key",
            "logical_key",
            unique=True,
            sqlite_where=text(
                "state IN ('preparing', 'ready', 'needs_attention', 'applying')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    logical_key: Mapped[str]
    """Normalized producer-owned identity, e.g. ``track:17`` or a collection key."""
    title: Mapped[str]
    scope_type: Mapped[str]
    scope_id: Mapped[int | None] = mapped_column(default=None)
    state: Mapped[str] = mapped_column(default="preparing")
    error: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    snapshots: Mapped[list[SourceSnapshot]] = relationship(
        back_populates="review_bundle", cascade="all, delete-orphan"
    )
    revisions: Mapped[list[ProposalRevision]] = relationship(
        back_populates="review_bundle", cascade="all, delete-orphan"
    )
    apply_runs: Mapped[list[ApplyRun]] = relationship(
        back_populates="review_bundle", cascade="all, delete-orphan"
    )
    task_attempts: Mapped[list[TaskAttempt]] = relationship(
        back_populates="review_bundle", cascade="all, delete-orphan"
    )
    asset_candidates: Mapped[list[AssetCandidate]] = relationship(
        back_populates="review_bundle", cascade="all, delete-orphan"
    )


class AssetCandidate(Base):
    """A validated cover blob selectable only inside its owning review."""

    __tablename__ = "asset_candidates"
    __table_args__ = (
        CheckConstraint("provider <> ''", name="ck_asset_candidates_provider_not_empty"),
        UniqueConstraint(
            "review_bundle_id", "blob_id", name="uq_asset_candidates_bundle_blob"
        ),
        Index("ix_asset_candidates_review_bundle_id", "review_bundle_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    review_bundle_id: Mapped[int] = mapped_column(
        ForeignKey("review_bundles.id", ondelete="CASCADE")
    )
    blob_id: Mapped[int] = mapped_column(ForeignKey("blobs.id", ondelete="CASCADE"))
    provider: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    review_bundle: Mapped[ReviewBundle] = relationship(back_populates="asset_candidates")
    blob: Mapped[Blob] = relationship()


class SourceSnapshot(Base):
    """Immutable normalized filesystem/catalog state observed before proposing writes."""

    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "review_bundle_id", "content_digest", name="uq_source_snapshots_bundle_digest"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    review_bundle_id: Mapped[int] = mapped_column(
        ForeignKey("review_bundles.id", ondelete="CASCADE")
    )
    content_digest: Mapped[str]
    payload: Mapped[dict[str, object]] = mapped_column(JSONDict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    review_bundle: Mapped[ReviewBundle] = relationship(back_populates="snapshots")


class ProposalRevision(Base):
    """Immutable proposal content; only its operations' user decisions may change."""

    __tablename__ = "proposal_revisions"
    __table_args__ = (
        UniqueConstraint(
            "review_bundle_id", "revision_no", name="uq_proposal_revisions_bundle_number"
        ),
        UniqueConstraint(
            "review_bundle_id",
            "parent_revision_no",
            "content_digest",
            name="uq_proposal_revisions_idempotent_successor",
        ),
        Index(
            "uq_proposal_revisions_current_bundle",
            "review_bundle_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    review_bundle_id: Mapped[int] = mapped_column(
        ForeignKey("review_bundles.id", ondelete="CASCADE")
    )
    source_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="RESTRICT")
    )
    revision_no: Mapped[int]
    parent_revision_no: Mapped[int] = mapped_column(default=0)
    content_digest: Mapped[str]
    is_current: Mapped[bool] = mapped_column(default=True)
    candidate_source: Mapped[str | None] = mapped_column(default=None)
    candidate_ref: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    review_bundle: Mapped[ReviewBundle] = relationship(back_populates="revisions")
    source_snapshot: Mapped[SourceSnapshot] = relationship()
    operations: Mapped[list[Operation]] = relationship(
        back_populates="proposal_revision", cascade="all, delete-orphan", order_by="Operation.seq"
    )
    apply_runs: Mapped[list[ApplyRun]] = relationship(back_populates="proposal_revision")
    candidate_url_aliases: Mapped[list[CandidateUrlAlias]] = relationship(
        back_populates="proposal_revision", cascade="all, delete-orphan"
    )


class CandidateUrlAlias(Base):
    """Non-semantic provider URL identity resolved to one immutable revision."""

    __tablename__ = "candidate_url_aliases"
    __table_args__ = (
        CheckConstraint(
            "candidate_type IN ('release', 'album', 'track')",
            name="ck_candidate_url_aliases_type",
        ),
        UniqueConstraint(
            "proposal_revision_id",
            "provider",
            "candidate_type",
            "provider_id",
            name="uq_candidate_url_aliases_revision_ref",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_revision_id: Mapped[int] = mapped_column(
        ForeignKey("proposal_revisions.id", ondelete="CASCADE")
    )
    provider: Mapped[str]
    candidate_type: Mapped[str]
    provider_id: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    proposal_revision: Mapped[ProposalRevision] = relationship(
        back_populates="candidate_url_aliases"
    )


class Operation(Base):
    """Persisted typed operation with observed and proposed values kept separate."""

    __tablename__ = "operations"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('set_tag', 'write_lyrics', 'embed_art', 'remove_art', "
            "'move_file', 'set_replay_gain', 'grouping_correction')",
            name="ck_operations_kind",
        ),
        CheckConstraint(
            "decision IN ('pending', 'accepted', 'rejected')",
            name="ck_operations_decision",
        ),
        CheckConstraint(
            "kind != 'write_lyrics' OR ("
            "COALESCE(json_type(proposed_value), '') = 'object' AND "
            "COALESCE(json_type(proposed_value, '$.text'), '') = 'text' AND "
            "COALESCE(json_type(proposed_value, '$.synced'), '') IN ('true', 'false') AND "
            "COALESCE(json_type(proposed_value, '$.provider'), '') = 'text' AND "
            "(COALESCE(json_type(current_value), 'null') = 'null' OR ("
            "COALESCE(json_type(current_value), '') = 'object' AND "
            "COALESCE(json_type(current_value, '$.text'), '') = 'text' AND "
            "COALESCE(json_type(current_value, '$.synced'), '') IN ('true', 'false') AND "
            "COALESCE(json_type(current_value, '$.provider'), '') = 'text')))",
            name="ck_operations_write_lyrics_value",
        ),
        CheckConstraint(
            "kind != 'embed_art' OR ("
            "COALESCE(json_type(proposed_value), '') = 'object' AND "
            "COALESCE(json_type(proposed_value, '$.blob_id'), '') = 'integer' AND "
            "(COALESCE(json_type(current_value), 'null') = 'null' OR ("
            "COALESCE(json_type(current_value), '') = 'object' AND "
            "COALESCE(json_type(current_value, '$.blob_id'), '') = 'integer')))",
            name="ck_operations_embed_art_value",
        ),
        CheckConstraint(
            "kind != 'remove_art' OR ("
            "COALESCE(json_type(proposed_value), 'null') = 'null' AND "
            "(COALESCE(json_type(current_value), 'null') = 'null' OR ("
            "COALESCE(json_type(current_value), '') = 'object' AND "
            "COALESCE(json_type(current_value, '$.blob_id'), '') = 'integer')))",
            name="ck_operations_remove_art_value",
        ),
        CheckConstraint(
            "kind != 'move_file' OR ("
            "COALESCE(json_type(current_value), '') = 'text' AND "
            "COALESCE(json_type(proposed_value), '') = 'text')",
            name="ck_operations_move_file_value",
        ),
        CheckConstraint(
            "kind != 'set_replay_gain' OR ("
            "COALESCE(json_type(proposed_value), '') IN ('integer', 'real') AND "
            "COALESCE(json_type(current_value), 'null') IN ('null', 'integer', 'real'))",
            name="ck_operations_replay_gain_value",
        ),
        UniqueConstraint("proposal_revision_id", "seq", name="uq_operations_revision_seq"),
        Index("ix_operations_proposal_revision_id", "proposal_revision_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_revision_id: Mapped[int] = mapped_column(
        ForeignKey("proposal_revisions.id", ondelete="CASCADE")
    )
    source_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="RESTRICT")
    )
    seq: Mapped[int]
    kind: Mapped[str]
    field: Mapped[str]
    target_type: Mapped[str]
    target_id: Mapped[int]
    current_value: Mapped[object | None] = mapped_column(JSON, default=None)
    proposed_value: Mapped[object | None] = mapped_column(JSON, default=None)
    decision: Mapped[str] = mapped_column(default="pending")
    provenance: Mapped[dict[str, object]] = mapped_column(JSONDict, default=dict)
    validation: Mapped[dict[str, object]] = mapped_column(JSONDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    proposal_revision: Mapped[ProposalRevision] = relationship(back_populates="operations")
    source_snapshot: Mapped[SourceSnapshot] = relationship()


class TaskAttempt(Base):
    """Per-item technical outcome; jobs remain the execution/lease mechanism."""

    __tablename__ = "task_attempts"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'running', 'succeeded', 'not_found', "
            "'transient_failure', 'permanent_failure', 'cancelled')",
            name="ck_task_attempts_state",
        ),
        CheckConstraint("attempt_no >= 1", name="ck_task_attempts_attempt_no"),
        UniqueConstraint(
            "review_bundle_id",
            "kind",
            "item_key",
            "attempt_no",
            name="uq_task_attempts_bundle_kind_item_number",
        ),
        Index("ix_task_attempts_bundle_state", "review_bundle_id", "state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    review_bundle_id: Mapped[int] = mapped_column(
        ForeignKey("review_bundles.id", ondelete="CASCADE")
    )
    proposal_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("proposal_revisions.id", ondelete="SET NULL"), default=None
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), default=None
    )
    kind: Mapped[str]
    item_key: Mapped[str]
    state: Mapped[str] = mapped_column(default="pending")
    attempt_no: Mapped[int] = mapped_column(default=1)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON, default=None)
    error: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    review_bundle: Mapped[ReviewBundle] = relationship(back_populates="task_attempts")
    proposal_revision: Mapped[ProposalRevision | None] = relationship()
    job: Mapped[Job | None] = relationship()


class ApplyRun(Base):
    """One persistent, idempotent attempt to apply a frozen proposal revision."""

    __tablename__ = "apply_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'applying', 'applied', 'partially_applied', 'failed')",
            name="ck_apply_runs_state",
        ),
        UniqueConstraint(
            "review_bundle_id", "idempotency_key", name="uq_apply_runs_bundle_idempotency"
        ),
        Index(
            "uq_apply_runs_active_bundle",
            "review_bundle_id",
            unique=True,
            sqlite_where=text("state IN ('pending', 'applying')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    review_bundle_id: Mapped[int] = mapped_column(
        ForeignKey("review_bundles.id", ondelete="CASCADE")
    )
    proposal_revision_id: Mapped[int] = mapped_column(
        ForeignKey("proposal_revisions.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str]
    state: Mapped[str] = mapped_column(default="pending")
    manifest: Mapped[dict[str, object]] = mapped_column(JSONDict, default=dict)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON, default=None)
    error: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    review_bundle: Mapped[ReviewBundle] = relationship(back_populates="apply_runs")
    proposal_revision: Mapped[ProposalRevision] = relationship(back_populates="apply_runs")
    operation_attempts: Mapped[list[OperationAttempt]] = relationship(
        back_populates="apply_run", cascade="all, delete-orphan"
    )


class OperationAttempt(Base):
    """Attempted value and outcome, separate from current/proposed operation state."""

    __tablename__ = "operation_attempts"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'applied', 'failed', 'conflicted', 'skipped')",
            name="ck_operation_attempts_state",
        ),
        UniqueConstraint("apply_run_id", "operation_id", name="uq_operation_attempts_run_op"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    apply_run_id: Mapped[int] = mapped_column(ForeignKey("apply_runs.id", ondelete="CASCADE"))
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id", ondelete="RESTRICT"))
    attempted_value: Mapped[object | None] = mapped_column(JSON, default=None)
    state: Mapped[str] = mapped_column(default="pending")
    error: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    apply_run: Mapped[ApplyRun] = relationship(back_populates="operation_attempts")
    operation: Mapped[Operation] = relationship()


class TrackFingerprintMatch(Base):
    """One AcoustID lookup result for one track (docs/PLAN.md §3's
    fingerprint short-circuit + §7b Stage 2 grouping).

    A single lookup returns several candidate recordings, each
    possibly linked to several releases — one row per (track,
    recording) pair, `mb_release_ids` carrying that recording's
    release list as JSON, so Stage 2 grouping can count how many
    *tracks* agree on a given release MBID without re-querying
    AcoustID every cascade run. Persisted separately from `tracks`
    (rather than a single mb_recording_id column there) because a
    track can have several plausible AcoustID candidates before
    grouping/matching picks one.
    """

    __tablename__ = "track_fingerprint_matches"
    __table_args__ = (Index("ix_track_fingerprint_matches_track_id", "track_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False
    )
    mb_recording_id: Mapped[str] = mapped_column(index=True)
    mb_release_ids: Mapped[tuple[str, ...]] = mapped_column(JSONList, default=())
    score: Mapped[float] = mapped_column(default=0.0)
    """AcoustID's own confidence score in [0, 1]."""

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


class Job(Base):
    """A unit of background work owned by the single writer worker
    (docs/PLAN.md §5, "Single-writer discipline"). API/CLI never write
    tracks/groups/changesets directly for anything long-running —
    they enqueue a Job and the worker's own session does the writing.

    `lease_until`/`worker_id` implement a lease rather than a hard lock:
    a worker claims a pending job by setting state='running' and
    lease_until=now+lease_seconds in one transaction; a crashed worker
    simply lets the lease expire, and `services.jobs.recover_stuck_jobs`
    resets any job whose lease has lapsed back to pending at the next
    startup — no separate heartbeat-missed detection needed while the
    process is alive, since a live worker renews its own lease.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_state_priority_created", "state", "priority", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str]
    """scan | fingerprint | group | match | import | apply_changeset |
    undo_changeset. Dispatched via jobs.registry, not a beets-style
    event bus (CLAUDE.md)."""
    state: Mapped[str] = mapped_column(default="pending")
    """pending | running | cancelling | succeeded | failed | cancelled"""
    priority: Mapped[int] = mapped_column(default=0)
    """Higher runs sooner; ties broken by created_at ascending."""

    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON, default=None)

    progress_current: Mapped[int] = mapped_column(default=0)
    progress_total: Mapped[int | None] = mapped_column(default=None)
    """None means indeterminate progress (e.g. a scan stage that
    doesn't know its file count up front) — the UI shows a spinner,
    not a bar, when this is None."""
    progress_message: Mapped[str | None] = mapped_column(default=None)

    attempts: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    worker_id: Mapped[str | None] = mapped_column(default=None)
    parent_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), default=None
    )
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    """Set by a cancel request; the running handler observes this
    cooperatively between pipeline stages and exits — the queue itself
    never force-kills a handler."""
    error: Mapped[str | None] = mapped_column(default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class JobEvent(Base):
    """Append-only progress/log record for one Job, replayable after an
    SSE client reconnects (docs/PLAN.md §9: "GET /api/jobs/{id}/events
    ?after=<seq> replays from job_events, that's why it's a table").

    The worker coalesces `progress` events to at most one per
    ~250ms per job (see jobs.progress.ProgressReporter) so a 40k-file
    scan doesn't write 40k rows; `log`/`state` events are not throttled
    since stage transitions and warnings are inherently sparse.
    """

    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_id_seq", "job_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    seq: Mapped[int]
    """Monotonic per job_id, assigned by jobs.queue.append_event."""
    kind: Mapped[str]
    """progress | log | state"""
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ImportSession(Base):
    """A resumable scan→fingerprint→group→match run over one library
    root (docs/PLAN.md §7, Phase 4's "resumable import session state
    machine"). One `import` Job orchestrates the whole session; the
    session's own `state` tracks the pipeline stage in progress so the
    UI can show a wizard-style step indicator independent of Job/
    JobEvent internals.

    State machine:
        pending -> scanning -> fingerprinting -> grouping -> matching
                -> reviewing -> completed
        (any state) -> failed
        (any non-terminal state) -> cancelled
    """

    __tablename__ = "import_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_root: Mapped[str]
    state: Mapped[str] = mapped_column(default="pending")
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), default=None
    )
    """The current/most recent orchestrator Job(type='import') driving
    this session — re-pointed to a new Job on resume."""
    stats: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    """Rolling counters: scanned/added/updated/errored, fingerprinted,
    groups_created, proposed_changesets, auto_applied."""
    error: Mapped[str | None] = mapped_column(default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    tasks: Mapped[list[ImportTask]] = relationship(
        back_populates="import_session",
        cascade="all, delete-orphan",
        order_by="ImportTask.seq",
    )


class ImportTask(Base):
    """One pipeline stage within an ImportSession.

    Stage granularity (not per-group) because stage boundaries are the
    natural resume points: a crash during fingerprinting resumes at
    fingerprinting, not mid-file. A per-group task table would need
    populating only after grouping already ran, which conflicts with
    being resumable from the very start of a session.
    """

    __tablename__ = "import_tasks"
    __table_args__ = (Index("ix_import_tasks_session_id", "import_session_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    import_session_id: Mapped[int] = mapped_column(
        ForeignKey("import_sessions.id", ondelete="CASCADE")
    )
    stage: Mapped[str]
    """scan | fingerprint | group | match"""
    seq: Mapped[int]
    """Fixed stage order (0-3) — handle_import walks tasks in this
    order and skips any already state='done' on resume."""
    state: Mapped[str] = mapped_column(default="pending")
    """pending | running | done | failed | skipped"""
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), default=None
    )
    result: Mapped[dict[str, object] | None] = mapped_column(JSON, default=None)
    error: Mapped[str | None] = mapped_column(default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    import_session: Mapped[ImportSession] = relationship(back_populates="tasks")


class DuplicateGroup(Base):
    """A set of tracks detected as the same recording at different
    bitrates/rips (docs/PLAN.md §Phase-6, "duplicate detection by
    fingerprint, not filename"). Detection only — there is no delete
    action; a per-file keep/discard decision needs product judgment
    (bitrate? format? tag completeness?) this feature doesn't make on
    the user's behalf. Not a ChangeSet: nothing here mutates a track
    or a file, so the diff/apply/undo machinery doesn't apply.

    Keyed on `mb_recording_id` (from AcoustID lookups, see
    `TrackFingerprintMatch`) rather than exact fingerprint-string
    equality — two different encodes of the same recording rarely
    produce byte-identical fingerprints, but AcoustID's own matching
    already accounts for that and gives every rip the same recording id.
    """

    __tablename__ = "duplicate_groups"
    __table_args__ = (
        Index("ix_duplicate_groups_mb_recording_id", "mb_recording_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mb_recording_id: Mapped[str] = mapped_column(unique=True)
    basis: Mapped[str] = mapped_column(default="acoustid")
    """Detection method — 'acoustid' today; a distinct value leaves
    room for a future non-fingerprint basis without a schema change."""
    dismissed: Mapped[bool] = mapped_column(default=False)
    """User marked this group as not actually duplicates (e.g. a live
    take AcoustID happens to match to the studio recording's id) —
    excluded from the default listing but never re-created, since the
    group is keyed by mb_recording_id and detection is idempotent."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    members: Mapped[list[DuplicateMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class DuplicateMember(Base):
    """One track belonging to a `DuplicateGroup`."""

    __tablename__ = "duplicate_members"
    __table_args__ = (
        UniqueConstraint("group_id", "track_id", name="uq_duplicate_member"),
        Index("ix_duplicate_members_group_id", "group_id"),
        Index("ix_duplicate_members_track_id", "track_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("duplicate_groups.id", ondelete="CASCADE")
    )
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))

    group: Mapped[DuplicateGroup] = relationship(back_populates="members")
    track: Mapped[Track | None] = relationship()


class Setting(Base):
    """DB-backed config overrides for the /settings screen (Phase 7
    suggestion #3, docs/PLAN.md §9) — the "settings DB table" the
    config precedence chain's docstring (config/schema.py's Config)
    anticipated but never built.

    Deliberately NOT a rearchitecture of Config/load_config(): bootstrap
    settings (storage.db_path, auth) must stay file/env-only, since
    load_config() runs before any DB connection exists — this table can
    only hold settings read *after* the DB is available. Plain key/JSON-
    value rows rather than one column per setting: keeps this table
    schema-stable as the covered setting surface grows, and mirrors
    settings/paths_guard.py-style small-surface-area services rather
    than adding a wide, sparse settings table.

    One row per logical setting key (e.g. "providers.musicbrainz",
    "paths.templates", "strip_fields"); services/settings.py owns the
    actual key names and value shapes. Provider tokens are stored in an
    owner-only secret store outside this database; provider rows contain
    only non-secret overrides and an opaque ``secret_ref``.  Startup migrates
    legacy plaintext ``token`` members before accepting requests."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
