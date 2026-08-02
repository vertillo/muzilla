"""Read-only API for the ReviewBundle foundation.

No legacy producer is moved by this route.  It makes the already-persisted contract
observable and generates the frontend's discriminated operation types.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from muzilla.api.deps import get_provider_set, get_session
from muzilla.api.schemas.manual_search import (
    CandidateUrlImportOut,
    CandidateUrlRefOut,
    CandidateUrlRequest,
    ManualCandidateImportRequest,
    ManualCandidateSearchOut,
    ManualCandidateSearchRequest,
    ProviderSearchCapabilityOut,
)
from muzilla.api.schemas.reviews import ReviewBundleDetailOut
from muzilla.services import manual_search as manual_search_service
from muzilla.services import reviews as reviews_service
from muzilla.services.providers import ProviderSet

router = APIRouter(tags=["reviews"])


def _candidate_url_error(exc: Exception) -> HTTPException:
    if isinstance(exc, manual_search_service.UrlCandidateFetchError):
        status_code = {
            "not_configured": 409,
            "not_found": 404,
            "invalid_credentials": 502,
            "temporary_unavailable": 503,
            "provider_failure": 502,
        }[exc.status]
        return HTTPException(
            status_code=status_code,
            detail={"code": exc.status, "message": str(exc)},
        )
    code = (
        "unsupported_candidate_type"
        if isinstance(exc, manual_search_service.UnsupportedCandidateUrl)
        else "invalid_candidate_url"
    )
    return HTTPException(status_code=400, detail={"code": code, "message": str(exc)})


@router.get("/reviews/{review_bundle_id}", response_model=ReviewBundleDetailOut)
async def get_review_bundle(
    review_bundle_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> reviews_service.ReviewBundleDetail:
    detail = reviews_service.get_review_bundle(session, review_bundle_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="review bundle not found")
    return detail


@router.get(
    "/reviews/{review_bundle_id}/candidates/capabilities",
    response_model=tuple[ProviderSearchCapabilityOut, ...],
)
async def get_manual_search_capabilities(
    review_bundle_id: int,
    session: Annotated[Session, Depends(get_session)],
    provider_set: Annotated[ProviderSet, Depends(get_provider_set)],
) -> tuple[manual_search_service.ProviderSearchCapability, ...]:
    try:
        return manual_search_service.capabilities_for_review(session, provider_set, review_bundle_id)
    except manual_search_service.ManualSearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/reviews/{review_bundle_id}/candidates/search",
    response_model=ManualCandidateSearchOut,
)
async def search_manual_candidates(
    review_bundle_id: int,
    body: ManualCandidateSearchRequest,
    session: Annotated[Session, Depends(get_session)],
    provider_set: Annotated[ProviderSet, Depends(get_provider_set)],
) -> manual_search_service.ManualSearchResult:
    try:
        return await manual_search_service.search(
            session,
            provider_set,
            review_bundle_id,
            manual_search_service.ManualSearchQuery(**body.model_dump()),
        )
    except manual_search_service.ManualSearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/reviews/{review_bundle_id}/candidates/import",
    response_model=ReviewBundleDetailOut,
)
async def import_manual_candidate(
    review_bundle_id: int,
    body: ManualCandidateImportRequest,
    session: Annotated[Session, Depends(get_session)],
    provider_set: Annotated[ProviderSet, Depends(get_provider_set)],
) -> reviews_service.ReviewBundleDetail:
    try:
        detail = await manual_search_service.import_candidate(
            session, provider_set, review_bundle_id, source=body.source, ref_id=body.ref_id
        )
    except (manual_search_service.ManualSearchError, reviews_service.ReviewInvariantError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return detail


@router.post(
    "/reviews/{review_bundle_id}/candidates/url/recognize",
    response_model=CandidateUrlRefOut,
)
async def recognize_candidate_url(
    review_bundle_id: int,
    body: CandidateUrlRequest,
    session: Annotated[Session, Depends(get_session)],
) -> manual_search_service.CandidateUrlRef:
    try:
        return manual_search_service.recognize_url_for_review(
            session, review_bundle_id, body.url
        )
    except (
        manual_search_service.CandidateUrlError,
        manual_search_service.ManualSearchError,
    ) as exc:
        raise _candidate_url_error(exc) from exc


@router.post(
    "/reviews/{review_bundle_id}/candidates/url/import",
    response_model=CandidateUrlImportOut,
)
async def import_candidate_url(
    review_bundle_id: int,
    body: CandidateUrlRequest,
    session: Annotated[Session, Depends(get_session)],
    provider_set: Annotated[ProviderSet, Depends(get_provider_set)],
) -> manual_search_service.UrlCandidateImportResult:
    try:
        result = await manual_search_service.import_url_candidate(
            session, provider_set, review_bundle_id, url=body.url
        )
    except (
        manual_search_service.CandidateUrlError,
        manual_search_service.ManualSearchError,
        reviews_service.ReviewInvariantError,
    ) as exc:
        raise _candidate_url_error(exc) from exc
    session.commit()
    return result
