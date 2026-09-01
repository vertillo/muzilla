"""Validated cover assets owned by one stable ReviewBundle.

Remote-only per ART-COVER-SOURCE-001: only artwork retrieved from supported remote
providers may be registered; local uploads are removed and legacy provider="upload"
candidates are rejected at the service boundary.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.config.schema import Config
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
]
