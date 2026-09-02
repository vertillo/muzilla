"""ReviewBundle API: typed review reads, enrichment decisions, and controlled apply."""

from __future__ import annotations

from typing import Annotated

from fastapi import (  # pyright: ignore[reportMissingImports]
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
)
from sqlalchemy.orm import Session  # pyright: ignore[reportMissingImports]

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
    CoverDecisionRequest,
    ReviewBundleDetailOut,
    ReviewBundlePageOut,
    ReviewNeighborsOut,
    ReviewOperationDecisionsRequest,
    ReviewOperationEditRequest,
    SkipReviewRequest,
    UndoReviewOut,
    UndoReviewRequest,
)
from muzilla.api.security import require_sensitive_mutation
from muzilla.config.schema import Config
from muzilla.services import cover_assets as cover_assets_service
from muzilla.services import jobs as jobs_service
from muzilla.services import manual_search as manual_search_service
from muzilla.services import review_apply as review_apply_service
from muzilla.services import review_undo as review_undo_service
from muzilla.services import reviews as reviews_service
from muzilla.services import settings as settings_service
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
    session_filter: Annotated[str | None, Query(alias="session")] = None,
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
            session_filter=session_filter,
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


@router.get("/reviews/{review_bundle_id}/neighbors", response_model=ReviewNeighborsOut)
async def get_review_neighbors(
    review_bundle_id: int,
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    state: str | None = None,
    confidence: str | None = None,
    issue: str | None = None,
    source: str | None = None,
    session_filter: Annotated[str | None, Query(alias="session")] = None,
) -> reviews_service.ReviewNeighbors:
    try:
        return reviews_service.review_neighbors(
            session,
            review_bundle_id,
            q=q,
            states=tuple(value for value in (state or "").split(",") if value),
            confidence=confidence,
            issue=issue,
            source=source,
            session_filter=session_filter,
        )
    except reviews_service.ReviewInvariantError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
            decisions=tuple(
                (decision.operation_id, decision.decision) for decision in body.decisions
            ),
        )
    except reviews_service.ReviewInvariantError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return detail


@router.post(
    "/reviews/{review_bundle_id}/operations/{operation_id}/edit",
    response_model=ReviewBundleDetailOut,
)
async def edit_review_operation(
    review_bundle_id: int,
    operation_id: int,
    body: ReviewOperationEditRequest,
    session: Annotated[Session, Depends(get_session)],
) -> reviews_service.ReviewBundleDetail:
    try:
        if body.kind == "set_tag":
            detail = reviews_service.edit_operation(
                session,
                review_bundle_id,
                operation_id=operation_id,
                revision_id=body.revision_id,
                kind=body.kind,
                value=body.value,
            )
        else:
            detail = reviews_service.edit_operation(
                session,
                review_bundle_id,
                operation_id=operation_id,
                revision_id=body.revision_id,
                kind=body.kind,
                value={"text": body.text, "synced": body.synced},
            )
    except reviews_service.ReviewInvariantError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    _sensitive: Annotated[None, Depends(require_sensitive_mutation)],
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
        msg = str(exc)
        # REVIEW-CONFLICTS-001: structured 409 for stale/concurrent
        if msg.startswith("stale_source:"):
            raise HTTPException(
                status_code=409, detail={"code": "stale_source", "message": msg}
            ) from exc
        if msg.startswith("concurrent_conflict:"):
            raise HTTPException(
                status_code=409, detail={"code": "concurrent_conflict", "message": msg}
            ) from exc
        raise HTTPException(status_code=409, detail=msg) from exc


@router.post(
    "/reviews/{review_bundle_id}/refresh",
    response_model=ReviewBundleDetailOut,
    status_code=200,
)
async def refresh_review_bundle(
    review_bundle_id: int,
    session: Annotated[Session, Depends(get_session)],
    _sensitive: Annotated[None, Depends(require_sensitive_mutation)],
) -> reviews_service.ReviewBundleDetail:
    """REVIEW-CONFLICTS-001: re-read source file facts and create new revision."""
    try:
        detail = reviews_service.refresh_review_bundle(session, review_bundle_id)
    except reviews_service.ReviewInvariantError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return detail


@router.post(
    "/reviews/{review_bundle_id}/skip",
    response_model=ReviewBundleDetailOut,
    status_code=200,
)
async def skip_review_bundle(
    review_bundle_id: int,
    body: SkipReviewRequest,
    session: Annotated[Session, Depends(get_session)],
) -> reviews_service.ReviewBundleDetail:
    """Explicit Skip / Leave unchanged — resolves without modifying files."""
    try:
        detail = reviews_service.skip_review_bundle(
            session, review_bundle_id, revision_id=body.revision_id
        )
    except reviews_service.ReviewInvariantError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return detail


@router.post(
    "/reviews/{review_bundle_id}/undo",
    response_model=UndoReviewOut,
    status_code=202,
)
async def undo_review_bundle(
    review_bundle_id: int,
    body: UndoReviewRequest,
    session: Annotated[Session, Depends(get_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    _sensitive: Annotated[None, Depends(require_sensitive_mutation)],
) -> review_undo_service.ReviewUndoEnqueued:
    try:
        return review_undo_service.enqueue_review_undo(
            session,
            review_bundle_id,
            apply_run_id=body.apply_run_id,
            idempotency_key=idempotency_key,
            backup=body.backup,
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


# Local cover upload removed per ART-COVER-SOURCE-001: only remote artwork from supported providers is allowed.
# The POST /cover/candidates endpoint is intentionally absent (404) to enforce remote-only sourcing.


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
        return manual_search_service.capabilities_for_review(
            session, provider_set, review_bundle_id
        )
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
    refresh: bool = False,
) -> manual_search_service.ManualSearchResult:
    try:
        return await manual_search_service.search(
            session,
            provider_set,
            review_bundle_id,
            manual_search_service.ManualSearchQuery(**body.model_dump()),
            refresh=refresh,
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
        effective_paths = settings_service.effective_paths_config(session, config.paths)
        detail = await manual_search_service.import_candidate(
            session,
            provider_set,
            review_bundle_id,
            source=body.source,
            ref_id=body.ref_id,
            paths_config=effective_paths,
            force=body.force,
        )
    except (manual_search_service.ManualSearchError, reviews_service.ReviewInvariantError) as exc:
        msg = str(exc).lower()
        if "confirmation required" in msg:
            raise HTTPException(
                status_code=409, detail={"code": "confirmation_required", "message": str(exc)}
            ) from exc
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
        return manual_search_service.recognize_url_for_review(session, review_bundle_id, body.url)
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
        effective_paths = settings_service.effective_paths_config(session, config.paths)
        result = await manual_search_service.import_url_candidate(
            session,
            provider_set,
            review_bundle_id,
            url=body.url,
            paths_config=effective_paths,
            force=body.force,
        )
    except (
        manual_search_service.CandidateUrlError,
        manual_search_service.ManualSearchError,
        reviews_service.ReviewInvariantError,
    ) as exc:
        if "confirmation required" in str(exc).lower():
            raise HTTPException(
                status_code=409, detail={"code": "confirmation_required", "message": str(exc)}
            ) from exc
        raise _candidate_url_error(exc) from exc
    session.commit()
    return result
