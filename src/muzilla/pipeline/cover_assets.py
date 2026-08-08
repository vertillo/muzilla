"""Persistent ownership boundary for cover candidates."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import AssetCandidate, Blob, ReviewBundle
from muzilla.domain.reviews import BundleState


class CoverAssetAssociationError(ValueError):
    pass


def register_candidate(
    session: Session,
    bundle_id: int,
    *,
    blob: Blob,
    provider: str,
) -> AssetCandidate:
    """Associate one already validated blob with a single open review."""
    bundle = session.get(ReviewBundle, bundle_id)
    if bundle is None:
        raise CoverAssetAssociationError(f"review bundle {bundle_id} not found")
    if BundleState(bundle.state) not in {
        BundleState.PREPARING,
        BundleState.READY,
        BundleState.NEEDS_ATTENTION,
    }:
        raise CoverAssetAssociationError("review bundle is not open for cover selection")
    if not provider.strip():
        raise CoverAssetAssociationError("cover candidate provider must not be empty")
    if blob.mime not in {"image/jpeg", "image/png"}:
        raise CoverAssetAssociationError(
            "cover candidate must be a validated JPEG or PNG"
        )
    if blob.width is None or blob.height is None or blob.width <= 0 or blob.height <= 0:
        raise CoverAssetAssociationError("cover candidate requires validated dimensions")
    existing = session.scalar(
        select(AssetCandidate).where(
            AssetCandidate.review_bundle_id == bundle_id,
            AssetCandidate.blob_id == blob.id,
        )
    )
    if existing is not None:
        return existing
    candidate = AssetCandidate(
        review_bundle_id=bundle_id,
        blob_id=blob.id,
        provider=provider.strip(),
    )
    session.add(candidate)
    session.flush()
    return candidate


def get_candidate(
    session: Session, bundle_id: int, candidate_id: int
) -> AssetCandidate | None:
    return session.scalar(
        select(AssetCandidate).where(
            AssetCandidate.id == candidate_id,
            AssetCandidate.review_bundle_id == bundle_id,
        )
    )


__all__ = ["CoverAssetAssociationError", "get_candidate", "register_candidate"]
