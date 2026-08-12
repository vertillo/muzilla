"""Blob serving: the only way api/cli read blob bytes for display
(album art thumbnails in the diff review UI, `/api/blobs/{id}?size=thumb`).

Returns raw bytes + mime rather than a dataclass wrapping db.models —
same boundary discipline as every other service, just shaped for a
binary HTTP response instead of JSON.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from muzilla.audio.art import ArtProcessingError, process_art
from muzilla.changes.blobstore import BlobStore
from muzilla.config.schema import Config

_THUMB_MAX_DIMENSION = 200


@dataclass(frozen=True, slots=True)
class BlobBytes:
    data: bytes
    mime: str


def get_blob_bytes(session: Session, config: Config, blob_id: int, *, thumb: bool) -> BlobBytes | None:
    store = BlobStore(config.storage.blob_dir)
    blob = store.get_by_id(session, blob_id)
    if blob is None:
        return None
    data = store.get_bytes(blob)
    if not thumb:
        return BlobBytes(data=data, mime=blob.mime)
    try:
        processed = process_art(data, max_dimension=_THUMB_MAX_DIMENSION)
    except ArtProcessingError:
        # Not every blob is a resizable image (unlikely today, but this
        # module makes no assumption); fall back to the original bytes
        # rather than 500ing the diff review screen over a thumbnail.
        return BlobBytes(data=data, mime=blob.mime)
    return BlobBytes(data=processed.data, mime=processed.mime)


__all__ = ["BlobBytes", "get_blob_bytes"]
