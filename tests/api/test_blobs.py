from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from muzilla.changes.blobstore import BlobStore
from muzilla.db.engine import create_db_engine, create_session_factory


def _jpeg_bytes(width: int = 300, height: int = 300) -> bytes:
    image = Image.new("RGB", (width, height), color=(10, 20, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _seed_blob(db_path: Path, blob_dir: Path, data: bytes) -> int:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    store = BlobStore(blob_dir)
    with factory() as session:
        blob = store.put(session, data, mime="image/jpeg", width=300, height=300)
        session.commit()
        return blob.id


def test_get_blob_returns_original_bytes(
    client: TestClient, migrated_db: Path, tmp_path: Path
) -> None:
    # tests/conftest.py's client fixture points MUZILLA_STORAGE__BLOB_DIR
    # at tmp_path / "blobs" — seed the same location.
    data = _jpeg_bytes()
    blob_id = _seed_blob(migrated_db, tmp_path / "blobs", data)

    resp = client.get(f"/api/blobs/{blob_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == data


def test_get_blob_thumb_is_resized(
    client: TestClient, migrated_db: Path, tmp_path: Path
) -> None:
    data = _jpeg_bytes(width=2000, height=2000)
    blob_id = _seed_blob(migrated_db, tmp_path / "blobs", data)

    resp = client.get(f"/api/blobs/{blob_id}?size=thumb")
    assert resp.status_code == 200
    decoded = Image.open(io.BytesIO(resp.content))
    assert decoded.width == 200
    assert decoded.height == 200


def test_get_blob_missing_404(client: TestClient) -> None:
    resp = client.get("/api/blobs/99999")
    assert resp.status_code == 404
