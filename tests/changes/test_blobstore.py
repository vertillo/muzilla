from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.blobstore import BlobStore
from muzilla.db.models import Blob


def test_put_is_content_addressed(db_session: Session, tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    blob1 = store.put(db_session, b"fake image bytes", mime="image/jpeg", width=100, height=100)
    blob2 = store.put(db_session, b"fake image bytes", mime="image/jpeg", width=100, height=100)
    db_session.commit()

    assert blob1.id == blob2.id  # same content -> same row, not duplicated
    assert blob1.sha256 == blob2.sha256


def test_put_stores_bytes_retrievable(db_session: Session, tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    blob = store.put(db_session, b"cover art bytes", mime="image/jpeg")
    db_session.commit()

    assert store.get_bytes(blob) == b"cover art bytes"
    assert store.get_path(blob).is_file()


def test_retain_and_release_refcounting(db_session: Session, tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    blob = store.put(db_session, b"shared cover", mime="image/jpeg")
    db_session.commit()
    assert blob.refcount == 0

    store.retain(db_session, blob)
    store.retain(db_session, blob)
    db_session.commit()
    assert blob.refcount == 2

    path = store.get_path(blob)
    assert path.is_file()

    store.release(db_session, blob)
    db_session.commit()
    assert blob.refcount == 1
    assert path.is_file()  # still referenced once

    store.release(db_session, blob)
    db_session.commit()
    assert not path.is_file()  # last reference dropped -> file removed


def test_different_content_different_blob(db_session: Session, tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    blob1 = store.put(db_session, b"cover A", mime="image/jpeg")
    blob2 = store.put(db_session, b"cover B", mime="image/jpeg")
    db_session.commit()

    assert blob1.id != blob2.id
    assert blob1.sha256 != blob2.sha256


def test_put_fsyncs_blob_before_publication_and_directory_after(
    db_session: Session, tmp_path: Path
) -> None:
    store = BlobStore(tmp_path / "blobs")
    events: list[str] = []
    real_fsync = os.fsync
    real_link = os.link

    def record_fsync(file_fd: int) -> None:
        kind = "directory" if stat.S_ISDIR(os.fstat(file_fd).st_mode) else "file"
        events.append(f"fsync-{kind}")
        real_fsync(file_fd)

    def record_link(source: str, destination: str, **kwargs: object) -> None:
        events.append("publish")
        real_link(source, destination, **kwargs)  # type: ignore[arg-type]

    with (
        patch("muzilla.changes.blobstore.os.fsync", side_effect=record_fsync),
        patch("muzilla.changes.blobstore.os.link", side_effect=record_link),
    ):
        store.put(db_session, b"durable image", mime="image/jpeg")

    publication = events.index("publish")
    assert "fsync-file" in events[:publication]
    assert "fsync-directory" in events[publication + 1 :]
    assert events.count("fsync-directory") >= 4


@pytest.mark.parametrize("failure", ["write", "file-fsync", "publication"])
def test_put_failures_do_not_publish_or_index_blob(
    db_session: Session, tmp_path: Path, failure: str
) -> None:
    store = BlobStore(tmp_path / "blobs")
    payload = b"must not be published"
    digest = hashlib.sha256(payload).hexdigest()
    target = store._shard_path(digest)

    with pytest.raises(OSError):
        if failure == "write":
            with patch("muzilla.changes.blobstore.os.write", side_effect=OSError("write fault")):
                store.put(db_session, payload, mime="image/jpeg")
        elif failure == "file-fsync":
            real_fsync = os.fsync

            def fail_file_fsync(file_fd: int) -> None:
                if not stat.S_ISDIR(os.fstat(file_fd).st_mode):
                    raise OSError("file fsync fault")
                real_fsync(file_fd)

            with patch("muzilla.changes.blobstore.os.fsync", side_effect=fail_file_fsync):
                store.put(db_session, payload, mime="image/jpeg")
        else:
            with patch(
                "muzilla.changes.blobstore.os.link", side_effect=OSError("publication fault")
            ):
                store.put(db_session, payload, mime="image/jpeg")

    assert not target.exists()
    assert db_session.scalar(select(Blob).where(Blob.sha256 == digest)) is None


def test_put_and_get_reject_corrupt_reused_blob(db_session: Session, tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    payload = b"valid image bytes"
    blob = store.put(db_session, payload, mime="image/jpeg")
    db_session.commit()
    store.get_path(blob).write_bytes(b"corrupt")

    with pytest.raises(OSError, match="integrity"):
        store.get_bytes(blob)
    with pytest.raises(OSError, match="integrity"):
        store.put(db_session, payload, mime="image/jpeg")


@pytest.mark.parametrize("reuse_kind", ["database-row", "orphan"])
def test_put_reused_blob_fsyncs_file_and_directory_chain_before_acceptance(
    db_session: Session, tmp_path: Path, reuse_kind: str
) -> None:
    store = BlobStore(tmp_path / "blobs")
    payload = b"existing but not known durable"
    blob = store.put(db_session, payload, mime="image/jpeg")
    db_session.commit()
    path = store.get_path(blob)
    if reuse_kind == "orphan":
        db_session.delete(blob)
        db_session.commit()

    file_stat = path.stat()
    directory_paths = {
        "blob-root": store.root,
        "first-shard": path.parent.parent,
        "blob-directory": path.parent,
    }
    directory_stats = {name: directory.stat() for name, directory in directory_paths.items()}
    events: list[str] = []
    real_fsync = os.fsync

    def record_fsync(file_fd: int) -> None:
        actual = os.fstat(file_fd)
        if (actual.st_dev, actual.st_ino) == (file_stat.st_dev, file_stat.st_ino):
            events.append("blob-file")
        else:
            for name, expected in directory_stats.items():
                if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino):
                    events.append(name)
                    break
        real_fsync(file_fd)

    with patch("muzilla.changes.blobstore.os.fsync", side_effect=record_fsync):
        reused = store.put(db_session, payload, mime="image/jpeg")

    assert reused.sha256 == blob.sha256
    file_sync = events.index("blob-file")
    assert {"blob-root", "first-shard", "blob-directory"} <= set(events[file_sync + 1 :])


@pytest.mark.parametrize("reuse_kind", ["database-row", "orphan"])
@pytest.mark.parametrize("failure_kind", ["blob-file", "blob-directory"])
def test_put_reused_blob_propagates_file_and_directory_fsync_failures(
    db_session: Session, tmp_path: Path, reuse_kind: str, failure_kind: str
) -> None:
    store = BlobStore(tmp_path / "blobs")
    payload = b"reused blob fsync failure"
    blob = store.put(db_session, payload, mime="image/jpeg")
    db_session.commit()
    path = store.get_path(blob)
    if reuse_kind == "orphan":
        db_session.delete(blob)
        db_session.commit()

    file_stat = path.stat()
    directory_stat = path.parent.stat()
    real_fsync = os.fsync

    def fail_fsync(file_fd: int) -> None:
        actual = os.fstat(file_fd)
        expected = file_stat if failure_kind == "blob-file" else directory_stat
        if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino):
            raise OSError(f"reused {failure_kind} fsync fault")
        real_fsync(file_fd)

    with (
        patch("muzilla.changes.blobstore.os.fsync", side_effect=fail_fsync),
        pytest.raises(OSError, match=f"reused {failure_kind} fsync fault"),
    ):
        store.put(db_session, payload, mime="image/jpeg")

    assert path.read_bytes() == payload
    assert (db_session.scalar(select(Blob).where(Blob.sha256 == blob.sha256)) is not None) is (
        reuse_kind == "database-row"
    )


def test_put_orphan_reuse_fsyncs_before_blob_row_flush(db_session: Session, tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    payload = b"orphan blob checkpoint order"
    blob = store.put(db_session, payload, mime="image/jpeg")
    db_session.commit()
    path = store.get_path(blob)
    db_session.delete(blob)
    db_session.commit()

    file_stat = path.stat()
    directory_paths = {
        "blob-root": store.root,
        "first-shard": path.parent.parent,
        "blob-directory": path.parent,
    }
    directory_stats = {name: directory.stat() for name, directory in directory_paths.items()}
    events: list[str] = []
    real_fsync = os.fsync
    real_flush = db_session.flush

    def record_fsync(file_fd: int) -> None:
        actual = os.fstat(file_fd)
        if (actual.st_dev, actual.st_ino) == (file_stat.st_dev, file_stat.st_ino):
            events.append("blob-file")
        else:
            for name, expected in directory_stats.items():
                if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino):
                    events.append(name)
                    break
        real_fsync(file_fd)

    def record_flush() -> None:
        events.append("db-flush")
        real_flush()

    with (
        patch("muzilla.changes.blobstore.os.fsync", side_effect=record_fsync),
        patch.object(db_session, "flush", side_effect=record_flush),
    ):
        reused = store.put(db_session, payload, mime="image/jpeg")

    assert reused.sha256 == blob.sha256
    checkpoint = events.index("db-flush")
    assert "blob-file" in events[:checkpoint]
    assert {"blob-root", "first-shard", "blob-directory"} <= set(events[:checkpoint])
