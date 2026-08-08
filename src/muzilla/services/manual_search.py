"""Manual candidate search and ReviewBundle import.

This service deliberately owns only the request-specific query, provider selection,
pagination and ReviewBundle adaptation.  Retrieval, hydration, scoring, duplicate
provenance, HTTP caching and provider adapters remain in Matching v2.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, cast

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from muzilla.config.schema import PathsConfig
from muzilla.db.models import CandidateUrlAlias, ReviewBundle
from muzilla.domain.reviews import BundleState
from muzilla.matching.candidates import ProviderSearchOutcome
from muzilla.pipeline.matching import (
    CandidateRow,
    search_group_candidates,
    search_track_candidates,
)
from muzilla.providers import url_registry
from muzilla.providers.base import (
    Capability,
    ProviderRef,
    ReleaseCandidate,
    ReleaseQuery,
    TrackCandidateProvider,
)
from muzilla.providers.set import ProviderSet
from muzilla.services import reviews as reviews_service
from muzilla.services.proposals import ProposalComposer, ProposalCompositionError

_METADATA_PROVIDERS = ("musicbrainz", "deezer", "discogs")
_MAX_PAGE_SIZE = 20
_MAX_PAGE = 9

CandidateUrlError = url_registry.CandidateUrlError
CandidateUrlRef = url_registry.CandidateUrlRef
UnsupportedCandidateUrl = url_registry.UnsupportedCandidateUrl
recognize_candidate_url = url_registry.recognize_candidate_url


class ManualSearchError(ValueError):
    pass


class UrlCandidateFetchError(ManualSearchError):
    def __init__(
        self,
        status: Literal[
            "not_configured",
            "not_found",
            "invalid_credentials",
            "temporary_unavailable",
            "provider_failure",
        ],
        message: str,
    ) -> None:
        self.status = status
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ManualSearchQuery:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    year: int | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    providers: tuple[str, ...] = ()
    page: int = 0
    page_size: int = 10

    def normalized(self) -> ManualSearchQuery:
        def clean(value: str | None) -> str | None:
            if value is None:
                return None
            normalized = " ".join(value.split())
            return normalized or None

        result = replace(
            self,
            title=clean(self.title),
            artist=clean(self.artist),
            album=clean(self.album),
            isrc=clean(self.isrc),
            providers=tuple(dict.fromkeys(provider.strip().lower() for provider in self.providers if provider.strip())),
        )
        if not any((result.title, result.artist, result.album, result.isrc)):
            raise ManualSearchError("provide at least one of title, artist, album, or ISRC")
        if result.year is not None and not 1000 <= result.year <= 9999:
            raise ManualSearchError("year must be between 1000 and 9999")
        if result.duration_ms is not None and result.duration_ms <= 0:
            raise ManualSearchError("duration_ms must be positive")
        if not 0 <= result.page <= _MAX_PAGE:
            raise ManualSearchError(f"page must be between 0 and {_MAX_PAGE}")
        if not 1 <= result.page_size <= _MAX_PAGE_SIZE:
            raise ManualSearchError(f"page_size must be between 1 and {_MAX_PAGE_SIZE}")
        return result

    def release_query(self) -> ReleaseQuery:
        return ReleaseQuery(
            title=self.title,
            artist=self.artist,
            album=self.album,
            year=self.year,
            duration_ms=self.duration_ms,
            isrc=self.isrc,
        )


@dataclass(frozen=True, slots=True)
class ProviderSearchCapability:
    provider: str
    status: Literal["available", "not_configured"]
    supports_search: bool


@dataclass(frozen=True, slots=True)
class ManualSearchResult:
    query: ManualSearchQuery
    candidates: tuple[CandidateRow, ...]
    provider_outcomes: tuple[ProviderSearchOutcome, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class UrlCandidateImportResult:
    candidate: CandidateUrlRef
    already_selected: bool
    review: reviews_service.ReviewBundleDetail


def provider_capabilities(provider_set: ProviderSet) -> tuple[ProviderSearchCapability, ...]:
    """Expose only supported metadata adapters and their effective availability."""
    capabilities: list[ProviderSearchCapability] = []
    for name in _METADATA_PROVIDERS:
        provider = provider_set.metadata.get(name)
        searchable = provider is not None and Capability.SEARCH_RELEASES in provider.capabilities
        capabilities.append(
            ProviderSearchCapability(
                provider=name,
                status="available" if searchable else "not_configured",
                supports_search=searchable,
            )
        )
    return tuple(capabilities)


def capabilities_for_review(
    session: Session, provider_set: ProviderSet, review_id: int
) -> tuple[ProviderSearchCapability, ...]:
    _review(session, review_id)
    return provider_capabilities(provider_set)


def _review(session: Session, review_id: int) -> ReviewBundle:
    review = session.get(ReviewBundle, review_id)
    if review is None:
        raise ManualSearchError("review bundle not found")
    if review.scope_type not in {"track", "group"} or review.scope_id is None:
        raise ManualSearchError("manual candidate search requires a track or group review")
    if review.state not in {state.value for state in (BundleState.PREPARING, BundleState.READY, BundleState.NEEDS_ATTENTION)}:
        raise ManualSearchError("review bundle is not open for candidate selection")
    return review


def _selected_metadata_providers(
    provider_set: ProviderSet, names: tuple[str, ...]
) -> tuple[tuple[str, ...], ProviderSet]:
    requested = names or _METADATA_PROVIDERS
    unknown = set(requested) - set(_METADATA_PROVIDERS)
    if unknown:
        raise ManualSearchError(f"unknown metadata provider: {sorted(unknown)[0]}")
    selected = {
        name: provider
        for name in requested
        if (provider := provider_set.metadata.get(name)) is not None
        and Capability.SEARCH_RELEASES in provider.capabilities
    }
    # ProviderSet is immutable at the boundary. Reusing its live clients preserves
    # the configured HTTP cache and adapter instances while narrowing this request.
    return requested, replace(provider_set, metadata=selected)


def _merge_outcomes(
    requested: tuple[str, ...],
    actual: tuple[ProviderSearchOutcome, ...],
) -> tuple[ProviderSearchOutcome, ...]:
    by_name = {outcome.provider: outcome for outcome in actual}
    return tuple(
        by_name.get(name, ProviderSearchOutcome(name, "not_configured")) for name in requested
    )


def _page_rows(rows: tuple[CandidateRow, ...], page: int, page_size: int) -> tuple[tuple[CandidateRow, ...], bool]:
    start = page * page_size
    end = start + page_size
    visible = rows[start:end]
    reindex = {old_index: new_index for new_index, old_index in enumerate(range(start, start + len(visible)))}
    # Duplicate references are list-local UI hints. A page must never expose an
    # index pointing to a row outside that page.
    page_rows = tuple(
        replace(row, is_duplicate_of=tuple(reindex[index] for index in row.is_duplicate_of if index in reindex))
        for row in visible
    )
    return page_rows, len(rows) > end


async def search(
    session: Session, provider_set: ProviderSet, review_id: int, query: ManualSearchQuery
) -> ManualSearchResult:
    review = _review(session, review_id)
    scope_id = review.scope_id
    assert scope_id is not None
    normalized = query.normalized()
    requested, selected_set = _selected_metadata_providers(provider_set, normalized.providers)
    retrieval_limit = (normalized.page + 1) * normalized.page_size

    if review.scope_type == "track":
        track_proposal = await search_track_candidates(
            session,
            selected_set,
            scope_id,
            normalized.release_query(),
            limit_per_provider=retrieval_limit,
            include_rejected=True,
        )
        ranked_rows = track_proposal.candidates
        actual_outcomes = track_proposal.provider_outcomes
    else:
        group_proposal = await search_group_candidates(
            session,
            selected_set,
            scope_id,
            normalized.release_query(),
            limit_per_provider=retrieval_limit,
            include_rejected=True,
        )
        ranked_rows = group_proposal.candidates
        actual_outcomes = group_proposal.provider_outcomes
    candidates, has_more = _page_rows(ranked_rows, normalized.page, normalized.page_size)
    return ManualSearchResult(
        query=normalized,
        candidates=candidates,
        provider_outcomes=_merge_outcomes(requested, actual_outcomes),
        has_more=has_more,
    )


async def import_candidate(
    session: Session,
    provider_set: ProviderSet,
    review_id: int,
    *,
    source: str,
    ref_id: str,
    paths_config: PathsConfig | None = None,
) -> reviews_service.ReviewBundleDetail:
    """Hydrate one chosen provider ID and replace the current revision idempotently."""
    review = _review(session, review_id)
    provider = provider_set.metadata.get(source)
    if provider is None or Capability.GET_RELEASE not in provider.capabilities:
        raise ManualSearchError(f"provider {source!r} is not configured for candidate retrieval")
    candidate = await provider.get_release(ProviderRef(provider=source, id=ref_id))
    if candidate is None:
        raise ManualSearchError(f"candidate {ref_id!r} was not found at {source!r}")
    return _import_hydrated_candidate(session, review, candidate, paths_config=paths_config)


def recognize_url_for_review(session: Session, review_id: int, url: str) -> CandidateUrlRef:
    _review(session, review_id)
    return recognize_candidate_url(url)


async def _fetch_url_candidate(
    provider_set: ProviderSet, recognized: CandidateUrlRef
) -> ReleaseCandidate:
    provider = provider_set.metadata.get(recognized.provider)
    if provider is None:
        raise UrlCandidateFetchError(
            "not_configured", f"provider {recognized.provider!r} is not configured"
        )
    try:
        if recognized.candidate_type == "track":
            if Capability.GET_TRACK not in provider.capabilities:
                raise UnsupportedCandidateUrl(recognized.provider, "track")
            track_provider = cast(TrackCandidateProvider, provider)
            candidate = await track_provider.get_track_candidate(
                ProviderRef(provider=recognized.provider, id=recognized.provider_id)
            )
        else:
            if Capability.GET_RELEASE not in provider.capabilities:
                raise UnsupportedCandidateUrl(
                    recognized.provider, recognized.candidate_type
                )
            candidate = await provider.get_release(
                ProviderRef(provider=recognized.provider, id=recognized.provider_id)
            )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            raise UrlCandidateFetchError(
                "invalid_credentials", "provider credentials were rejected"
            ) from exc
        if status_code in {408, 425, 429} or status_code >= 500:
            raise UrlCandidateFetchError(
                "temporary_unavailable", "provider is temporarily unavailable"
            ) from exc
        raise UrlCandidateFetchError("provider_failure", "provider request failed") from exc
    except httpx.RequestError as exc:
        raise UrlCandidateFetchError(
            "temporary_unavailable", "provider is temporarily unavailable"
        ) from exc
    if candidate is None:
        raise UrlCandidateFetchError("not_found", "provider candidate was not found")
    return candidate


async def import_url_candidate(
    session: Session,
    provider_set: ProviderSet,
    review_id: int,
    *,
    url: str,
    paths_config: PathsConfig | None = None,
) -> UrlCandidateImportResult:
    """Recognize locally, then fetch only through a configured provider ID method."""
    review = _review(session, review_id)
    recognized = recognize_candidate_url(url)
    existing = reviews_service.get_review_bundle(session, review_id)
    if existing is None:  # pragma: no cover - _review loaded this row above
        raise reviews_service.ReviewInvariantError("could not load review bundle")

    current_revision_id = existing.current_revision.id
    if _has_candidate_url_alias(session, current_revision_id, recognized):
        return UrlCandidateImportResult(recognized, True, existing)

    if (
        recognized.candidate_type != "track"
        and existing.current_revision.candidate_source == recognized.provider
        and existing.current_revision.candidate_ref == recognized.provider_id
    ):
        _remember_candidate_url_alias(session, current_revision_id, recognized)
        return UrlCandidateImportResult(recognized, True, existing)

    candidate = await _fetch_url_candidate(provider_set, recognized)
    already_selected = (
        existing.current_revision.candidate_source == candidate.source
        and existing.current_revision.candidate_ref == candidate.ref.id
    )
    if already_selected:
        _remember_candidate_url_alias(session, current_revision_id, recognized)
        return UrlCandidateImportResult(recognized, True, existing)
    detail = _import_hydrated_candidate(session, review, candidate, paths_config=paths_config)
    _remember_candidate_url_alias(
        session, detail.current_revision.id, recognized
    )
    return UrlCandidateImportResult(recognized, False, detail)


def _has_candidate_url_alias(
    session: Session, revision_id: int, candidate_url: CandidateUrlRef
) -> bool:
    return (
        session.scalar(
            select(CandidateUrlAlias.id).where(
                CandidateUrlAlias.proposal_revision_id == revision_id,
                CandidateUrlAlias.provider == candidate_url.provider,
                CandidateUrlAlias.candidate_type == candidate_url.candidate_type,
                CandidateUrlAlias.provider_id == candidate_url.provider_id,
            )
        )
        is not None
    )


def _remember_candidate_url_alias(
    session: Session, revision_id: int, candidate_url: CandidateUrlRef
) -> None:
    session.execute(
        sqlite_insert(CandidateUrlAlias)
        .values(
            proposal_revision_id=revision_id,
            provider=candidate_url.provider,
            candidate_type=candidate_url.candidate_type,
            provider_id=candidate_url.provider_id,
        )
        .on_conflict_do_nothing()
    )


def _import_hydrated_candidate(
    session: Session,
    review: ReviewBundle,
    candidate: ReleaseCandidate,
    *,
    paths_config: PathsConfig | None = None,
) -> reviews_service.ReviewBundleDetail:
    """Adapt one hydrated candidate through the shared bundle composer."""
    try:
        return ProposalComposer(session, paths_config=paths_config).compose_candidate(review, candidate)
    except ProposalCompositionError as exc:
        raise ManualSearchError(str(exc)) from exc
