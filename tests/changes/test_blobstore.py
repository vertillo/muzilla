from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.changes.blobstore import BlobStore


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
