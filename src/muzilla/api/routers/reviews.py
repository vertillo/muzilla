"""Read-only API for the ReviewBundle foundation.

No legacy producer is moved by this route.  It makes the already-persisted contract
observable and generates the frontend's discriminated operation types.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from muzilla.api.deps import get_session
from muzilla.api.schemas.reviews import ReviewBundleDetailOut
from muzilla.services import reviews as reviews_service

router = APIRouter(tags=["reviews"])


@router.get("/reviews/{review_bundle_id}", response_model=ReviewBundleDetailOut)
async def get_review_bundle(
    review_bundle_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> reviews_service.ReviewBundleDetail:
    detail = reviews_service.get_review_bundle(session, review_bundle_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="review bundle not found")
    return detail
