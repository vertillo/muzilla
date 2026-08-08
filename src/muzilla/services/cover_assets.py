"""Validated cover assets owned by one stable ReviewBundle."""

from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.audio.art import (
    ArtMediaTypeError,
    ArtProcessingError,
    ArtSizeError,
    process_uploaded_art,
)
from muzilla.changes.blobstore import BlobStore
from muzilla.config.schema import Config
from muzilla.db.models import AssetCandidate
from muzilla.pipeline.cover_assets import (
    CoverAssetAssociationError,
    get_candidate,
    register_candidate,
)
from muzilla.services.blobs import BlobBytes, get_blob_bytes

CoverAssetError = CoverAssetAssociationError


class CoverAssetTooLarge(CoverAssetError):
    pass


class CoverAssetMediaTypeError(CoverAssetError):
    pass


class InvalidCoverAsset(CoverAssetError):
    pass


def upload_candidate(
    session: Session,
    config: Config,
    bundle_id: int,
    *,
    data: bytes,
    declared_mime: str,
) -> AssetCandidate:
    if len(data) > config.enrichment.art_upload_max_bytes:
        raise CoverAssetTooLarge("cover upload exceeds the configured byte limit")
    try:
        processed = process_uploaded_art(
            data,
            declared_mime=declared_mime,
            max_dimension=config.enrichment.art_embed_max_dimension,
            max_source_dimension=config.enrichment.art_upload_max_dimension,
            max_source_pixels=config.enrichment.art_upload_max_pixels,
        )
    except ArtMediaTypeError as exc:
        raise CoverAssetMediaTypeError(str(exc)) from exc
    except ArtSizeError as exc:
        raise CoverAssetTooLarge(str(exc)) from exc
    except ArtProcessingError as exc:
        raise InvalidCoverAsset(str(exc)) from exc
    blob = BlobStore(config.storage.blob_dir).put(
        session,
        processed.data,
        mime=processed.mime,
        width=processed.width,
        height=processed.height,
    )
    # A content-identical legacy blob may predate dimension persistence.
    # The normalized bytes were just decoded above, so these facts are trusted.
    blob.mime = processed.mime
    blob.size = len(processed.data)
    blob.width = processed.width
    blob.height = processed.height
    session.flush()
    return register_candidate(session, bundle_id, blob=blob, provider="upload")


def get_candidate_bytes(
    session: Session,
    config: Config,
    bundle_id: int,
    candidate_id: int,
    *,
    thumb: bool,
) -> BlobBytes | None:
    candidate = get_candidate(session, bundle_id, candidate_id)
    if candidate is None:
        return None
    return get_blob_bytes(session, config, candidate.blob_id, thumb=thumb)


__all__ = [
    "CoverAssetError",
    "CoverAssetMediaTypeError",
    "CoverAssetTooLarge",
    "InvalidCoverAsset",
    "get_candidate",
    "get_candidate_bytes",
    "register_candidate",
    "upload_candidate",
]
