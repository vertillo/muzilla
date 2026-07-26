"""Content-addressed blob store for binary payloads (album art).

Blobs live on disk, sharded by sha256 prefix — never inline in SQLite,
which would bloat the DB file and wreck WAL checkpointing (docs/PLAN.md
§5). The `blobs` table (db/models.py) is the index: sha256 (unique),
mime, size, dimensions, storage_path, refcount. This module owns both
sides of that split.

Refcounting exists so a 20MB cover shared across a 12-track album is
stored once, not 12 times — `retain()`/`release()` bump/decrement the
count, and `release()` deletes the on-disk file once it hits zero.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import Blob


class BlobStore:
    """A content-addressed store rooted at `root`.

    Directory layout: `<root>/<sha256[:2]>/<sha256[2:4]>/<sha256>` —
    two levels of 256-way sharding keeps any one directory from growing
    unwieldy even at library scale.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _shard_path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256[2:4] / sha256

    def put(
        self,
        session: Session,
        data: bytes,
        *,
        mime: str,
        width: int | None = None,
        height: int | None = None,
    ) -> Blob:
        """Store `data`, creating a new Blob row (refcount=0) if this
        content hasn't been seen before, or returning the existing row.
        Callers must call `retain()` once they attach a reference (e.g.
        `art_blob_id` on a track, or `before_blob`'s art in the apply
        journal) — `put()` alone does not bump refcount, so orphaned
        uploads that are never attached don't leak a phantom reference.
        """
        sha256 = hashlib.sha256(data).hexdigest()
        existing = session.scalar(select(Blob).where(Blob.sha256 == sha256))
        if existing is not None:
            return existing

        path = self._shard_path(sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_bytes(data)
        os.replace(tmp_path, path)

        blob = Blob(
            sha256=sha256,
            mime=mime,
            size=len(data),
            width=width,
            height=height,
            storage_path=str(path.relative_to(self.root)),
            refcount=0,
        )
        session.add(blob)
        session.flush()
        return blob

    def get_bytes(self, blob: Blob) -> bytes:
        return (self.root / blob.storage_path).read_bytes()

    def get_path(self, blob: Blob) -> Path:
        return self.root / blob.storage_path

    def retain(self, session: Session, blob: Blob) -> None:
        blob.refcount += 1
        session.flush()

    def release(self, session: Session, blob: Blob) -> None:
        """Decrement refcount; delete the on-disk file and row once it
        reaches zero. A no-op below zero rather than raising — callers
        should never double-release, but this keeps a bug from cascading
        into a crash during an apply/undo path."""
        if blob.refcount <= 0:
            return
        blob.refcount -= 1
        session.flush()
        if blob.refcount == 0:
            path = self.root / blob.storage_path
            path.unlink(missing_ok=True)
            session.delete(blob)
            session.flush()

    def get_by_id(self, session: Session, blob_id: int) -> Blob | None:
        return session.get(Blob, blob_id)
