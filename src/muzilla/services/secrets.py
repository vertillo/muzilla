"""Persistent secret storage outside the exported application database.

The filesystem containing ``root`` is the authority: the database stores only
opaque references.  Files are replaced atomically and are never interpolated
into errors or logs.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Protocol

_REFERENCE_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_SECRET_FILE_PATTERN = re.compile(r"[0-9a-f]{64}\.secret\Z")


class SecretStoreError(RuntimeError):
    """A sanitized secret-store failure safe to return without secret data."""


class SecretStore(Protocol):
    def get(self, reference: str) -> str | None: ...

    def set(self, reference: str, value: str) -> None: ...

    def delete(self, reference: str) -> None: ...

    def clear(self) -> None: ...


class FileSecretStore:
    """One opaque, owner-only file per validated logical reference."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._ensure_root()

    def get(self, reference: str) -> str | None:
        path = self._path_for(reference)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SecretStoreError(f"cannot open secret reference {reference!r}") from exc

        try:
            metadata = os.fstat(descriptor)
            self._validate_secret_metadata(reference, metadata)
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = -1
                payload = stream.read()
        finally:
            if descriptor >= 0:
                os.close(descriptor)

        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretStoreError(f"secret reference {reference!r} is not valid UTF-8") from exc

    def set(self, reference: str, value: str) -> None:
        if not value:
            raise SecretStoreError("secret values must not be empty")
        try:
            payload = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SecretStoreError("secret value is not valid UTF-8") from exc
        self._ensure_root()
        destination = self._path_for(reference)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".pending-", dir=self.root)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            os.chmod(destination, 0o600, follow_symlinks=False)
            self._fsync_root()
        except OSError as exc:
            raise SecretStoreError(f"cannot persist secret reference {reference!r}") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            with suppress(FileNotFoundError):
                temporary.unlink()

    def delete(self, reference: str) -> None:
        path = self._path_for(reference)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise SecretStoreError(f"secret reference {reference!r} is not a regular file")
        try:
            path.unlink()
            self._fsync_root()
        except OSError as exc:
            raise SecretStoreError(f"cannot delete secret reference {reference!r}") from exc

    def clear(self) -> None:
        """Remove every credential file managed by this store, fail-closed.

        Preflight the full directory before unlinking anything so an unexpected file,
        symlink, owner, or permission cannot turn factory reset into a broad delete.
        """
        self._ensure_root()
        entries = list(self.root.iterdir())
        for path in entries:
            metadata = path.lstat()
            name_is_managed = (
                _SECRET_FILE_PATTERN.fullmatch(path.name) is not None
                or path.name.startswith(".pending-")
            )
            if not name_is_managed or not stat.S_ISREG(metadata.st_mode):
                raise SecretStoreError("provider secret directory contains an unmanaged entry")
            if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise SecretStoreError("provider secret directory contains an unsafe entry")
        try:
            for path in entries:
                path.unlink()
            self._fsync_root()
        except OSError as exc:
            raise SecretStoreError("cannot clear provider credentials") from exc

    def _ensure_root(self) -> None:
        try:
            metadata = self.root.lstat()
        except FileNotFoundError:
            try:
                self.root.mkdir(mode=0o700, parents=True)
                metadata = self.root.lstat()
            except OSError as exc:
                raise SecretStoreError("cannot create the provider secret directory") from exc
        if not stat.S_ISDIR(metadata.st_mode):
            raise SecretStoreError("provider secret path is not a directory")
        if metadata.st_uid != os.geteuid():
            raise SecretStoreError("provider secret directory has the wrong owner")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            try:
                os.chmod(self.root, 0o700, follow_symlinks=False)
            except OSError as exc:
                raise SecretStoreError("cannot secure the provider secret directory") from exc

    def _path_for(self, reference: str) -> Path:
        if _REFERENCE_PATTERN.fullmatch(reference) is None:
            raise SecretStoreError("invalid secret reference")
        digest = hashlib.sha256(reference.encode("ascii")).hexdigest()
        return self.root / f"{digest}.secret"

    @staticmethod
    def _validate_secret_metadata(reference: str, metadata: os.stat_result) -> None:
        if not stat.S_ISREG(metadata.st_mode):
            raise SecretStoreError(f"secret reference {reference!r} is not a regular file")
        if metadata.st_uid != os.geteuid():
            raise SecretStoreError(f"secret reference {reference!r} has the wrong owner")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise SecretStoreError(f"secret reference {reference!r} has unsafe permissions")

    def _fsync_root(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(self.root, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = ["FileSecretStore", "SecretStore", "SecretStoreError"]
