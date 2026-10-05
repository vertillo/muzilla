"""Content-addressed blob store for binary payloads (album art).

Blobs live on disk, sharded by sha256 prefix — never inline in SQLite,
which would bloat the DB file and wreck WAL checkpointing. The `blobs` table
(db/models.py) is the index: sha256 (unique), mime, size, dimensions,
storage_path, refcount. This module owns both sides of that split.

Refcounting exists so a 20MB cover shared across a 12-track album is stored
once, not once per track. `retain()`/`release()` manage those references.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import Blob

_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class BlobStore:
    """A content-addressed store rooted at `root`.

    Directory layout: `<root>/<sha256[:2]>/<sha256[2:4]>/<sha256>`.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _shard_path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256[2:4] / sha256

    def _open_directory(self, path: Path, *, create: bool) -> int:
        absolute = Path(os.path.abspath(path))
        descriptor = os.open(absolute.anchor, _DIRECTORY_FLAGS)
        try:
            for component in absolute.parts[1:]:
                if create:
                    try:
                        os.mkdir(component, 0o755, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    else:
                        # The child name must be durable before its contents can be used.
                        os.fsync(descriptor)
                child = os.open(component, _DIRECTORY_FLAGS | _NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _verified_read_at(
        directory_fd: int,
        name: str,
        *,
        expected_sha256: str,
        expected_size: int,
        sync_file: bool = False,
    ) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | _NOFOLLOW
        file_fd = os.open(name, flags, dir_fd=directory_fd)
        try:
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise OSError("blob integrity check failed: not a regular file")
            digest = hashlib.sha256()
            chunks: list[bytes] = []
            size = 0
            while chunk := os.read(file_fd, 1024 * 1024):
                digest.update(chunk)
                chunks.append(chunk)
                size += len(chunk)
            after = os.fstat(file_fd)
            entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_dev != entry.st_dev
                or before.st_ino != entry.st_ino
                or size != expected_size
                or digest.hexdigest() != expected_sha256
            ):
                raise OSError("blob integrity check failed: size or SHA-256 mismatch")
            if sync_file:
                os.fsync(file_fd)
            return b"".join(chunks)
        finally:
            os.close(file_fd)

    def _sync_blob_directory_chain(self, path: Path) -> None:
        absolute = Path(os.path.abspath(path))
        root = Path(os.path.abspath(self.root))
        try:
            absolute.relative_to(root)
        except ValueError as exc:
            raise OSError("blob directory is outside the configured root") from exc

        descriptors = [os.open(absolute.anchor, _DIRECTORY_FLAGS)]
        try:
            for component in absolute.parts[1:]:
                descriptors.append(
                    os.open(
                        component,
                        _DIRECTORY_FLAGS | _NOFOLLOW,
                        dir_fd=descriptors[-1],
                    )
                )
            root_index = len(root.parts) - 1
            # Sync child directory entries before their parents, up through the blob root.
            for descriptor in reversed(descriptors[root_index:]):
                os.fsync(descriptor)
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _read_blob(self, blob: Blob, *, establish_durability: bool = False) -> bytes:
        relative = self._relative_blob_path(blob)
        directory = self.root / relative.parent
        directory_fd = self._open_directory(directory, create=False)
        try:
            data = self._verified_read_at(
                directory_fd,
                relative.name,
                expected_sha256=blob.sha256,
                expected_size=blob.size,
                sync_file=establish_durability,
            )
            if establish_durability:
                self._sync_blob_directory_chain(directory)
            return data
        finally:
            os.close(directory_fd)

    @staticmethod
    def _relative_blob_path(blob: Blob) -> Path:
        if len(blob.sha256) != 64 or any(c not in "0123456789abcdef" for c in blob.sha256):
            raise OSError("blob integrity check failed: invalid SHA-256 identifier")
        expected = Path(blob.sha256[:2]) / blob.sha256[2:4] / blob.sha256
        actual = Path(blob.storage_path)
        if actual.is_absolute() or actual != expected or blob.size < 0:
            raise OSError("blob integrity check failed: invalid storage metadata")
        return expected

    def put(
        self,
        session: Session,
        data: bytes,
        *,
        mime: str,
        width: int | None = None,
        height: int | None = None,
    ) -> Blob:
        """Durably publish `data` before creating its database reference."""
        sha256 = hashlib.sha256(data).hexdigest()
        existing = session.scalar(select(Blob).where(Blob.sha256 == sha256))
        if existing is not None:
            if existing.size != len(data):
                raise OSError("blob integrity check failed: database size mismatch")
            self._read_blob(existing, establish_durability=True)
            return existing

        path = self._shard_path(sha256)
        directory_fd = self._open_directory(path.parent, create=True)
        temp_name: str | None = None
        temp_fd: int | None = None
        published = False
        try:
            try:
                self._verified_read_at(
                    directory_fd,
                    path.name,
                    expected_sha256=sha256,
                    expected_size=len(data),
                    sync_file=True,
                )
            except FileNotFoundError:
                pass
            else:
                # A previous process may have published the bytes before its DB commit.
                # Revalidate and checkpoint the file before making a row reference.
                published = True

            if not published:
                flags = (
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | _NOFOLLOW
                )
                for _ in range(8):
                    candidate = f".{sha256}.{secrets.token_hex(12)}.tmp"
                    try:
                        temp_fd = os.open(candidate, flags, 0o600, dir_fd=directory_fd)
                    except FileExistsError:
                        continue
                    temp_name = candidate
                    break
                if temp_fd is None or temp_name is None:
                    raise FileExistsError("could not allocate exclusive blob temporary")

                view = memoryview(data)
                while view:
                    chunk = view[: 1024 * 1024]
                    written = os.write(temp_fd, chunk)
                    if written <= 0:
                        raise OSError("short write while storing blob")
                    view = view[written:]
                os.fsync(temp_fd)

                # Link publication is atomic and refuses to replace an unexpected entry.
                try:
                    os.link(
                        temp_name,
                        path.name,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    published = True
                except FileExistsError:
                    self._verified_read_at(
                        directory_fd,
                        path.name,
                        expected_sha256=sha256,
                        expected_size=len(data),
                        sync_file=True,
                    )
                    published = True
                os.fsync(directory_fd)
                entry = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
                if not stat.S_ISREG(entry.st_mode):
                    raise OSError("blob integrity check failed after publication")
                os.unlink(temp_name, dir_fd=directory_fd)
                temp_name = None
                os.fsync(directory_fd)

            # Historical/reused shard directories may not have been synced when created.
            # Establish the complete directory-entry chain after the blob file is durable.
            self._sync_blob_directory_chain(path.parent)
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
        finally:
            if temp_name is not None and temp_fd is not None:
                try:
                    entry = os.stat(temp_name, dir_fd=directory_fd, follow_symlinks=False)
                    temp = os.fstat(temp_fd)
                    if entry.st_dev == temp.st_dev and entry.st_ino == temp.st_ino:
                        os.unlink(temp_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            if temp_fd is not None:
                os.close(temp_fd)
            os.close(directory_fd)

    def get_bytes(self, blob: Blob) -> bytes:
        return self._read_blob(blob)

    def get_durable_bytes(self, blob: Blob) -> bytes:
        """Verify the blob and persist its file and shard entries before reuse."""
        return self._read_blob(blob, establish_durability=True)

    def get_path(self, blob: Blob) -> Path:
        return self.root / self._relative_blob_path(blob)

    def retain(self, session: Session, blob: Blob) -> None:
        blob.refcount += 1
        session.flush()

    def release(self, session: Session, blob: Blob) -> None:
        """Decrement refcount; delete the on-disk file and row once it hits zero."""
        if blob.refcount <= 0:
            return
        blob.refcount -= 1
        session.flush()
        if blob.refcount == 0:
            path = self.get_path(blob)
            path.unlink(missing_ok=True)
            session.delete(blob)
            session.flush()

    def get_by_id(self, session: Session, blob_id: int) -> Blob | None:
        return session.get(Blob, blob_id)
