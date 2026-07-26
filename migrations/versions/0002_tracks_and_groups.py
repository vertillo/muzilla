"""tracks, track_groups, and FTS5 search index

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "track_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("grouping_basis", sa.String(), nullable=True),
        sa.Column("grouping_confidence", sa.Float(), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("album", sa.String(), nullable=True),
        sa.Column("album_artist", sa.String(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("original_year", sa.Integer(), nullable=True),
        sa.Column("track_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disc_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("catalog_number", sa.String(), nullable=True),
        sa.Column("barcode", sa.String(), nullable=True),
        sa.Column("expected_track_count", sa.Integer(), nullable=True),
        sa.Column("mb_release_id", sa.String(), nullable=True),
        sa.Column("discogs_release_id", sa.String(), nullable=True),
        sa.Column("art_blob_id", sa.Integer(), nullable=True),
        sa.Column("is_compilation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("match_state", sa.String(), nullable=False, server_default="unmatched"),
        sa.Column("match_distance", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_track_groups_key", "track_groups", ["key"], unique=True)
    op.create_index("ix_track_groups_album", "track_groups", ["album"])
    op.create_index("ix_track_groups_album_artist", "track_groups", ["album_artist"])
    op.create_index("ix_track_groups_mb_release_id", "track_groups", ["mb_release_id"])

    op.create_table(
        "tracks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("ext", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("mtime_ns", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("tag_hash", sa.String(), nullable=True),
        sa.Column("format", sa.String(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("bitrate", sa.Integer(), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("channels", sa.Integer(), nullable=True),
        sa.Column("codec", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("artist", sa.String(), nullable=True),
        sa.Column("artists", sa.JSON(), nullable=False),
        sa.Column("album", sa.String(), nullable=True),
        sa.Column("album_artist", sa.String(), nullable=True),
        sa.Column("composer", sa.String(), nullable=True),
        sa.Column("track_no", sa.Integer(), nullable=True),
        sa.Column("track_total", sa.Integer(), nullable=True),
        sa.Column("disc_no", sa.Integer(), nullable=True),
        sa.Column("disc_total", sa.Integer(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("original_year", sa.Integer(), nullable=True),
        sa.Column("date", sa.String(), nullable=True),
        sa.Column("compilation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("catalog_number", sa.String(), nullable=True),
        sa.Column("barcode", sa.String(), nullable=True),
        sa.Column("isrc", sa.String(), nullable=True),
        sa.Column("country", sa.String(), nullable=True),
        sa.Column("media", sa.String(), nullable=True),
        sa.Column("genre", sa.JSON(), nullable=False),
        sa.Column("mood", sa.JSON(), nullable=False),
        sa.Column("bpm", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(), nullable=True),
        sa.Column("mb_track_id", sa.String(), nullable=True),
        sa.Column("mb_release_id", sa.String(), nullable=True),
        sa.Column("mb_recording_id", sa.String(), nullable=True),
        sa.Column("mb_artist_id", sa.String(), nullable=True),
        sa.Column("discogs_release_id", sa.String(), nullable=True),
        sa.Column("deezer_track_id", sa.String(), nullable=True),
        sa.Column("acoustid_id", sa.String(), nullable=True),
        sa.Column("acoustid_fingerprint", sa.String(), nullable=True),
        sa.Column("rg_track_gain", sa.Float(), nullable=True),
        sa.Column("rg_track_peak", sa.Float(), nullable=True),
        sa.Column("rg_album_gain", sa.Float(), nullable=True),
        sa.Column("rg_album_peak", sa.Float(), nullable=True),
        sa.Column("r128_track_gain", sa.Float(), nullable=True),
        sa.Column("comment", sa.String(), nullable=True),
        sa.Column("encoder", sa.String(), nullable=True),
        sa.Column("has_lyrics", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lyrics_synced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_embedded_art", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("art_blob_id", sa.Integer(), nullable=True),
        sa.Column("extra_tags", sa.JSON(), nullable=False),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("track_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("probe_error", sa.String(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_written_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("uq_tracks_path", "tracks", ["path"], unique=True)
    op.create_index("ix_tracks_group_id", "tracks", ["group_id"])
    op.create_index("ix_tracks_mb_release_id", "tracks", ["mb_release_id"])
    op.create_index("ix_tracks_tag_hash", "tracks", ["tag_hash"])

    # FTS5 virtual table for catalog search. content='tracks' makes it a
    # contentless-adjacent "external content" table: FTS5 stores only the
    # index, tracks stays the source of truth, and triggers below keep
    # the two in sync on every insert/update/delete.
    op.execute(
        """
        CREATE VIRTUAL TABLE tracks_fts USING fts5(
            title, artist, album, album_artist,
            content='tracks', content_rowid='id'
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER tracks_fts_ai AFTER INSERT ON tracks BEGIN
            INSERT INTO tracks_fts(rowid, title, artist, album, album_artist)
            VALUES (new.id, new.title, new.artist, new.album, new.album_artist);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER tracks_fts_ad AFTER DELETE ON tracks BEGIN
            INSERT INTO tracks_fts(tracks_fts, rowid, title, artist, album, album_artist)
            VALUES ('delete', old.id, old.title, old.artist, old.album, old.album_artist);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER tracks_fts_au AFTER UPDATE ON tracks BEGIN
            INSERT INTO tracks_fts(tracks_fts, rowid, title, artist, album, album_artist)
            VALUES ('delete', old.id, old.title, old.artist, old.album, old.album_artist);
            INSERT INTO tracks_fts(rowid, title, artist, album, album_artist)
            VALUES (new.id, new.title, new.artist, new.album, new.album_artist);
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tracks_fts_au")
    op.execute("DROP TRIGGER IF EXISTS tracks_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS tracks_fts_ai")
    op.execute("DROP TABLE IF EXISTS tracks_fts")
    op.drop_index("ix_tracks_tag_hash", table_name="tracks")
    op.drop_index("ix_tracks_mb_release_id", table_name="tracks")
    op.drop_index("ix_tracks_group_id", table_name="tracks")
    op.drop_index("uq_tracks_path", table_name="tracks")
    op.drop_table("tracks")
    op.drop_index("ix_track_groups_mb_release_id", table_name="track_groups")
    op.drop_index("ix_track_groups_album_artist", table_name="track_groups")
    op.drop_index("ix_track_groups_album", table_name="track_groups")
    op.drop_index("ix_track_groups_key", table_name="track_groups")
    op.drop_table("track_groups")
