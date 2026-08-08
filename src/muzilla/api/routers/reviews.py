"""ReviewBundle API: typed review reads, enrichment decisions, and controlled apply."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from muzilla.api.deps import get_config, get_provider_set, get_session
from muzilla.api.schemas.jobs import JobEnqueuedOut
from muzilla.api.schemas.manual_search import (
    CandidateUrlImportOut,
    CandidateUrlRefOut,
    CandidateUrlRequest,
    ManualCandidateImportRequest,
    ManualCandidateSearchOut,
    ManualCandidateSearchRequest,
    ProviderSearchCapabilityOut,
)
from muzilla.api.schemas.reviews import (
    ApplyReviewOut,
    ApplyReviewRequest,
    AssetCandidateOut,
    CoverDecisionRequest,
    ReviewBundleDetailOut,
    ReviewBundlePageOut,
    ReviewOperationDecisionsRequest,
)
from muzilla.config.schema import Config
from muzilla.services import cover_assets as cover_assets_service
from muzilla.services import jobs as jobs_service
from muzilla.services import manual_search as manual_search_service
from muzilla.services import review_apply as review_apply_service
from muzilla.services import reviews as reviews_service
from muzilla.services.proposals import ProposalComposer, ProposalCompositionError
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


@router.get("/reviews", response_model=ReviewBundlePageOut)
async def list_review_bundles(
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    state: str | None = None,
    confidence: str | None = None,
    issue: str | None = None,
    source: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> reviews_service.ReviewBundlePage:
    states = tuple(value for value in (state or "").split(",") if value)
    try:
        return reviews_service.list_review_bundles(
            session,
            q=q,
            states=states,
            confidence=confidence,
            issue=issue,
            source=source,
            cursor=cursor,
            limit=min(max(limit, 1), 200),
        )
    except reviews_service.ReviewInvariantError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reviews/{review_bundle_id}", response_model=ReviewBundleDetailOut)
async def get_review_bundle(
    review_bundle_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> reviews_service.ReviewBundleDetail:
    detail = reviews_service.get_review_bundle(session, review_bundle_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="review bundle not found")
    return detail


@router.patch("/reviews/{review_bundle_id}/operations", response_model=ReviewBundleDetailOut)
async def patch_review_operation_decisions(
    review_bundle_id: int,
    body: ReviewOperationDecisionsRequest,
    session: Annotated[Session, Depends(get_session)],
) -> reviews_service.ReviewBundleDetail:
    try:
        detail = reviews_service.apply_operation_decisions(
            session,
            review_bundle_id,
            revision_id=body.revision_id,
            decisions=tuple((decision.operation_id, decision.decision) for decision in body.decisions),
        )
    except reviews_service.ReviewInvariantError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return detail


@router.post(
    "/reviews/{review_bundle_id}/apply",
    response_model=ApplyReviewOut,
    status_code=202,
)
async def apply_review_bundle(
    review_bundle_id: int,
    session: Annotated[Session, Depends(get_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    body: ApplyReviewRequest | None = None,
) -> review_apply_service.ReviewApplyEnqueued:
    try:
        return review_apply_service.enqueue_review_apply(
            session,
            review_bundle_id,
            idempotency_key=idempotency_key,
            backup=body.backup if body is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/reviews/{review_bundle_id}/cover",
    response_model=ReviewBundleDetailOut,
)
async def choose_cover(
    review_bundle_id: int,
    body: CoverDecisionRequest,
    session: Annotated[Session, Depends(get_session)],
) -> reviews_service.ReviewBundleDetail:
    try:
        detail = ProposalComposer(session).choose_cover(
            review_bundle_id,
            action=body.action,
            asset_candidate_id=body.asset_candidate_id,
        )
    except ProposalCompositionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return detail


async def _read_cover_body(request: Request, *, max_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            parsed_content_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
        if parsed_content_length > max_bytes:
            raise cover_assets_service.CoverAssetTooLarge(
                "cover upload exceeds the configured byte limit"
            )
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise cover_assets_service.CoverAssetTooLarge(
                "cover upload exceeds the configured byte limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/reviews/{review_bundle_id}/cover/candidates",
    response_model=AssetCandidateOut,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/png": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
async def upload_cover_candidate(
    review_bundle_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[Config, Depends(get_config)],
) -> reviews_service.AssetCandidateDetail:
    try:
        data = await _read_cover_body(
            request, max_bytes=config.enrichment.art_upload_max_bytes
        )
        candidate = cover_assets_service.upload_candidate(
            session,
            config,
            review_bundle_id,
            data=data,
            declared_mime=request.headers.get("content-type", ""),
        )
        detail = reviews_service.get_asset_candidate_detail(
            session, review_bundle_id, candidate.id
        )
        if detail is None:
            raise cover_assets_service.CoverAssetError("could not load cover candidate")
    except cover_assets_service.CoverAssetMediaTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except cover_assets_service.CoverAssetTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except cover_assets_service.InvalidCoverAsset as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except cover_assets_service.CoverAssetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return detail


@router.get("/reviews/{review_bundle_id}/cover/candidates/{candidate_id}/thumbnail")
async def get_cover_candidate_thumbnail(
    review_bundle_id: int,
    candidate_id: int,
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[Config, Depends(get_config)],
) -> Response:
    result = cover_assets_service.get_candidate_bytes(
        session,
        config,
        review_bundle_id,
        candidate_id,
        thumb=True,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="cover candidate not found")
    return Response(
        content=result.data,
        media_type=result.mime,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.post(
    "/reviews/{review_bundle_id}/tasks/{kind}/retry",
    response_model=JobEnqueuedOut,
    status_code=202,
)
async def retry_review_task(
    review_bundle_id: int,
    kind: str,
    session: Annotated[Session, Depends(get_session)],
) -> JobEnqueuedOut:
    try:
        job = jobs_service.retry_review_task(session, review_bundle_id, kind=kind)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JobEnqueuedOut(job_id=job.id)


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
    config: Annotated[Config, Depends(get_config)],
) -> reviews_service.ReviewBundleDetail:
    try:
        detail = await manual_search_service.import_candidate(
            session,
            provider_set,
            review_bundle_id,
            source=body.source,
            ref_id=body.ref_id,
            paths_config=config.paths,
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
    config: Annotated[Config, Depends(get_config)],
) -> manual_search_service.UrlCandidateImportResult:
    try:
        result = await manual_search_service.import_url_candidate(
            session, provider_set, review_bundle_id, url=body.url, paths_config=config.paths
        )
    except (
        manual_search_service.CandidateUrlError,
        manual_search_service.ManualSearchError,
        reviews_service.ReviewInvariantError,
    ) as exc:
        raise _candidate_url_error(exc) from exc
    session.commit()
    return result
