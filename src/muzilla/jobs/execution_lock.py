"""Per-job OS locks guarding an entire worker execution across processes."""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on unsupported platforms
    fcntl = None  # type: ignore[assignment]


class JobExecutionLockError(RuntimeError):
    """Execution-lock storage is unsafe or POSIX locking is unavailable."""


class JobExecutionLock:
    """Holds one process-released advisory lock; lock files are never unlinked."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._closed = False

    @classmethod
    def try_for_session_factory(
        cls, session_factory: sessionmaker[Session], job_id: int
    ) -> Self | None:
        bind = session_factory.kw.get("bind")
        return cls.try_acquire(_lock_directory(bind), job_id)

    @classmethod
    def try_for_session(cls, session: Session, job_id: int) -> Self | None:
        return cls.try_acquire(_lock_directory(session.get_bind()), job_id)

    @classmethod
    def try_acquire(cls, directory: Path, job_id: int) -> Self | None:
        _require_flock()
        _secure_directory(directory)
        path = directory / f"{job_id}.lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except OSError as exc:
            raise JobExecutionLockError(f"cannot safely open job execution lock: {exc}") from exc
        try:
            _secure_regular_file(fd, path)
            try:
                _flock(fd, _exclusive_nonblocking_flags())
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    os.close(fd)
                    return None
                raise
            return cls(fd)
        except Exception:
            os.close(fd)
            raise

    def close(self) -> None:
        if self._closed:
            return
        _require_flock()
        try:
            _flock(self._fd, _unlock_flag())
        finally:
            os.close(self._fd)
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _lock_directory(bind: object) -> Path:
    url = getattr(bind, "url", None)
    database = getattr(url, "database", None)
    if database and database != ":memory:":
        db_path = Path(str(database)).expanduser().resolve()
        return db_path.parent / f".{db_path.name}.job-locks"
    # An in-memory database cannot be shared by another process; retain thread
    # exclusion for its Engine without creating a lock in /data.
    return Path(tempfile.gettempdir()) / (f"muzilla-memory-job-locks-{os.getpid()}-{id(bind):x}")


def _require_flock() -> None:
    if fcntl is None or not hasattr(fcntl, "flock"):
        raise JobExecutionLockError("job execution requires POSIX flock support")


def _exclusive_nonblocking_flags() -> int:
    if fcntl is None or not hasattr(fcntl, "flock"):
        raise JobExecutionLockError("job execution requires POSIX flock support")
    return fcntl.LOCK_EX | fcntl.LOCK_NB


def _unlock_flag() -> int:
    if fcntl is None or not hasattr(fcntl, "flock"):
        raise JobExecutionLockError("job execution requires POSIX flock support")
    return fcntl.LOCK_UN


def _flock(fd: int, operation: int) -> None:
    if fcntl is None or not hasattr(fcntl, "flock"):
        raise JobExecutionLockError("job execution requires POSIX flock support")
    fcntl.flock(fd, operation)


def _secure_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise JobExecutionLockError(f"cannot create job execution lock directory: {exc}") from exc
    try:
        info = path.lstat()
    except OSError as exc:
        raise JobExecutionLockError(f"cannot inspect job execution lock directory: {exc}") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise JobExecutionLockError(
            "job execution lock directory must be owner-only and non-symlink"
        )


def _secure_regular_file(fd: int, path: Path) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise JobExecutionLockError(f"job execution lock must be a private regular file: {path}")
