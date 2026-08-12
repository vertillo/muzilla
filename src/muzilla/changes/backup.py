"""Pre-write file backups: copy originals before the first write.

Distinct from changes/blobstore.py's content-addressed art store: a
backup must preserve the library's relative layout (so a stranger
recovering from a bad apply can find "the file that used to be at
Artist/foo.mp3" without cross-referencing a hash), not shard by content
hash. Dedup here is a same-file guard, not art-style sharing across many
owners — a track's original is backed up once, ever, keyed by its
    already-computed `content_hash` (a cheap first/last-64KB+size hash), not
    re-read and re-hashed on every apply.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


class BackupError(Exception):
    """Raised when a pre-write backup could not be made. Callers must
    treat this as fatal for that file's write — a backup that silently
    didn't happen is worse than no backup feature at all."""


class BackupStore:
    """Rooted at `root`; layout mirrors `library_root`'s relative paths
    exactly (e.g. `library_root/Artist/foo.mp3` backs up to
    `root/Artist/foo.mp3`)."""

    def __init__(self, root: Path, *, library_root: Path) -> None:
        self.root = root
        self.library_root = library_root

    def _backup_path(self, source: Path, content_hash: str) -> Path:
        try:
            rel = source.resolve().relative_to(self.library_root.resolve())
        except ValueError:
            # Source isn't under library_root (shouldn't happen given the
            # applier's own guardrails, but never silently mis-key a
            # backup) — fall back to a name keyed by content_hash rather
            # than raising, since "no backup" is worse than "backup in
            # the wrong place." A bare `source.name` is NOT actually recoverable
            # by content_hash despite the comment that used to claim
            # so — this store keys backups by PATH, not by hash (unlike
            # blobstore.py, it is not content-addressed), so two
            # out-of-library files sharing a basename (e.g. two different
            # "track01.mp3") silently overwrote each other's backup, with
            # the second one destroying the first before its own write
            # even completed. Keying the fallback name on content_hash
            # itself makes two different files with the same basename
            # land at two different paths, and makes the claim in this
            # comment actually true.
            rel = Path(f"{source.name}.{content_hash}")
        return self.root / rel

    def already_backed_up(self, source: Path, content_hash: str) -> bool:
        marker = self._marker_path(source, content_hash)
        return marker.exists() and marker.read_text().strip() == content_hash

    def _marker_path(self, source: Path, content_hash: str) -> Path:
        backup_path = self._backup_path(source, content_hash)
        return backup_path.with_name(backup_path.name + ".muzilla-backup-hash")

    def backup(self, source: Path, content_hash: str) -> Path:
        """Copies `source` into the backup root if not already backed up
        for this exact `content_hash`. Idempotent per (path, hash) pair —
        safe to call on every apply, not just the first.

        Raises BackupError on any failure; never partially writes (copies
        to a temp name first, then renames into place)."""
        backup_path = self._backup_path(source, content_hash)
        if self.already_backed_up(source, content_hash):
            return backup_path

        try:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = backup_path.with_name(backup_path.name + ".muzilla-backup-tmp")
            shutil.copy2(source, tmp_path)
            with tmp_path.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(tmp_path, backup_path)
            marker = self._marker_path(source, content_hash)
            marker_tmp = marker.with_name(marker.name + ".tmp")
            marker_tmp.write_text(content_hash)
            with marker_tmp.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(marker_tmp, marker)
            directory_fd = os.open(backup_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise BackupError(f"failed to back up {source}: {exc}") from exc
        return backup_path
