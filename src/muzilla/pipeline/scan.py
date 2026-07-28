"""Filesystem scan: walk a library root, probe files, upsert into the DB.

The scan is the only place that touches the filesystem in Phase 1 — it
never writes tags, only reads them. Two invariants drive the shape of
this module:

- **Never abort on one bad file.** A single corrupt or unreadable track
  in a 50k-file library must not stop the scan; it gets recorded via
  `Track.probe_error` and the walk continues.
- **Vanished files are marked, not deleted.** A track missing on a
  rescan gets `missing_since` set so history/undo (Phase 2+) survive a
  library reorganization or a temporarily unmounted drive.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from unicodedata import normalize as unicode_normalize

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.domain.metadata import TrackMeta
from muzilla.domain.metadata import tag_hash as _domain_tag_hash
from muzilla.tags.hashing import partial_content_hash
from muzilla.tags.reader import TagReadError, read_track

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aiff", ".aif"}

_DEFAULT_IGNORE_DIR_NAMES = {".git", "@eaDir", "$RECYCLE.BIN", ".Trash-1000"}

_BATCH_SIZE = 500


@dataclass(frozen=True, slots=True)
class ScanStats:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    errored: int = 0
    missing: int = 0


def _walk_audio_files(
    root: Path, *, follow_symlinks: bool, ignore_dir_names: set[str]
) -> Iterator[Path]:
    """os.scandir-based walk, filtered to audio extensions.

    Uses scandir directly (not Path.rglob) so directory entries' stat
    info comes from the same syscall as the listing, which matters on
    a 100k-file library.
    """
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=follow_symlinks):
                if entry.name in ignore_dir_names:
                    continue
                stack.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=follow_symlinks):
                ext = Path(entry.name).suffix.lower()
                if ext in AUDIO_EXTENSIONS:
                    yield Path(entry.path)


def _normalize_path(path: Path) -> str:
    """NFC-normalized absolute path string — the DB's join key."""
    return unicode_normalize("NFC", str(path.resolve()))


def _tag_hash(meta: TrackMeta) -> str:
    """blake2b of the canonical tag serialization — the drift-detection
    check the apply path (Phase 2, changes/conflicts.py) uses. Delegates
    to domain.metadata.tag_hash so scan-time and apply-time hashes are
    guaranteed identical."""
    return _domain_tag_hash(meta)


_FORMAT_BY_EXT = {
    ".mp3": "MP3",
    ".flac": "FLAC",
    ".ogg": "OGG",
    ".opus": "Opus",
    ".m4a": "MP4",
    ".wav": "WAV",
    ".aiff": "AIFF",
    ".aif": "AIFF",
}


def _meta_to_track_fields(meta: TrackMeta, ext: str) -> dict[str, object]:
    """Maps TrackMeta onto the Track columns that mirror it 1:1.

    `format` (a human-readable container label, e.g. "FLAC") is derived
    from the extension rather than `meta.codec` — codec holds mutagen's
    internal class name (e.g. "OggVorbis"), which is an implementation
    detail rather than the label users expect in a format breakdown.
    """
    return {
        "format": _FORMAT_BY_EXT.get(ext),
        "title": meta.title,
        "artist": meta.artist,
        "artists": meta.artists,
        "album": meta.album,
        "album_artist": meta.album_artist,
        "composer": meta.composer,
        "track_no": meta.track_no,
        "track_total": meta.track_total,
        "disc_no": meta.disc_no,
        "disc_total": meta.disc_total,
        "year": meta.year,
        "original_year": meta.original_year,
        "date": meta.date,
        "compilation": meta.compilation,
        "label": meta.label,
        "catalog_number": meta.catalog_number,
        "barcode": meta.barcode,
        "isrc": meta.isrc,
        "country": meta.country,
        "media": meta.media,
        "genre": meta.genre,
        "mood": meta.mood,
        "bpm": meta.bpm,
        "key": meta.key,
        "mb_track_id": meta.mb_track_id,
        "mb_release_id": meta.mb_release_id,
        "mb_recording_id": meta.mb_recording_id,
        "mb_artist_id": meta.mb_artist_id,
        "discogs_release_id": meta.discogs_release_id,
        "deezer_track_id": meta.deezer_track_id,
        "acoustid_id": meta.acoustid_id,
        "acoustid_fingerprint": meta.acoustid_fingerprint,
        "rg_track_gain": meta.rg_track_gain,
        "rg_track_peak": meta.rg_track_peak,
        "rg_album_gain": meta.rg_album_gain,
        "rg_album_peak": meta.rg_album_peak,
        "r128_track_gain": meta.r128_track_gain,
        "comment": meta.comment,
        "encoder": meta.encoder,
        "has_embedded_art": meta.has_embedded_art,
        "has_lyrics": meta.has_lyrics,
        "extra_tags": meta.extra_tags,
        "duration_ms": meta.duration_ms,
        "bitrate": meta.bitrate,
        "sample_rate": meta.sample_rate,
        "channels": meta.channels,
        "codec": meta.codec,
    }


def scan_library(
    session: Session,
    root: Path,
    *,
    follow_symlinks: bool = False,
    ignore_dir_names: set[str] | None = None,
) -> ScanStats:
    """Walks `root`, probes each audio file, and upserts into `tracks`.

    Commits are batched (~500 rows) to keep transactions short on large
    libraries. Files whose (size_bytes, mtime_ns) match the existing row
    are skipped entirely (no tag read, no hash) — the fast path that
    keeps a rescan of an unchanged 100k-file library to seconds.
    """
    ignore = ignore_dir_names if ignore_dir_names is not None else _DEFAULT_IGNORE_DIR_NAMES
    stats = ScanStats()
    seen_paths: set[str] = set()
    pending = 0

    existing_by_path = {t.path: t for t in session.scalars(select(Track))}

    for file_path in _walk_audio_files(root, follow_symlinks=follow_symlinks, ignore_dir_names=ignore):
        try:
            stat = file_path.stat()
        except OSError:
            continue

        norm_path = _normalize_path(file_path)
        seen_paths.add(norm_path)
        size_bytes = stat.st_size
        mtime_ns = stat.st_mtime_ns

        existing = existing_by_path.get(norm_path)
        if (
            existing is not None
            and existing.missing_since is None
            and existing.size_bytes == size_bytes
            and existing.mtime_ns == mtime_ns
        ):
            stats = replace(stats, scanned=stats.scanned + 1, unchanged=stats.unchanged + 1)
            continue

        try:
            meta = read_track(file_path)
        except TagReadError as exc:
            stats = replace(stats, scanned=stats.scanned + 1, errored=stats.errored + 1)
            if existing is not None:
                existing.probe_error = str(exc)
                existing.size_bytes = size_bytes
                existing.mtime_ns = mtime_ns
                existing.missing_since = None
                existing.last_scanned_at = datetime.now(UTC)
            else:
                new_track = Track(
                    path=norm_path,
                    filename=file_path.name,
                    ext=file_path.suffix.lower(),
                    size_bytes=size_bytes,
                    mtime_ns=mtime_ns,
                    probe_error=str(exc),
                )
                session.add(new_track)
                existing_by_path[norm_path] = new_track
            pending += 1
            if pending >= _BATCH_SIZE:
                session.commit()
                pending = 0
            continue

        content_hash = partial_content_hash(file_path, size_bytes)
        tag_hash = _tag_hash(meta)

        if existing is not None:
            existing.filename = file_path.name
            existing.ext = file_path.suffix.lower()
            existing.size_bytes = size_bytes
            existing.mtime_ns = mtime_ns
            existing.content_hash = content_hash
            existing.tag_hash = tag_hash
            existing.probe_error = None
            existing.missing_since = None
            existing.last_scanned_at = datetime.now(UTC)
            for field_name, value in _meta_to_track_fields(meta, file_path.suffix.lower()).items():
                setattr(existing, field_name, value)
            stats = replace(stats, scanned=stats.scanned + 1, updated=stats.updated + 1)
        else:
            new_track = Track(
                path=norm_path,
                filename=file_path.name,
                ext=file_path.suffix.lower(),
                size_bytes=size_bytes,
                mtime_ns=mtime_ns,
                content_hash=content_hash,
                tag_hash=tag_hash,
                **_meta_to_track_fields(meta, file_path.suffix.lower()),
            )
            session.add(new_track)
            existing_by_path[norm_path] = new_track
            stats = replace(stats, scanned=stats.scanned + 1, added=stats.added + 1)

        pending += 1
        if pending >= _BATCH_SIZE:
            session.commit()
            pending = 0

    if pending:
        session.commit()

    vanished_paths = [p for p in existing_by_path if p not in seen_paths]
    if vanished_paths:
        now = datetime.now(UTC)
        for chunk_start in range(0, len(vanished_paths), _BATCH_SIZE):
            chunk = vanished_paths[chunk_start : chunk_start + _BATCH_SIZE]
            session.execute(
                update(Track)
                .where(Track.path.in_(chunk), Track.missing_since.is_(None))
                .values(missing_since=now)
            )
            session.commit()
        stats = replace(stats, missing=len(vanished_paths))

    return stats
