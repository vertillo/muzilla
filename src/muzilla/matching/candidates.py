"""Multi-source candidate gathering — one release, one source.

Queries several providers in parallel, scores each candidate
independently, and ranks them into one flat list. Never merges fields
across candidates: a candidate is one release from one provider, and
picking it applies that release's tags wholesale. Two candidates
describing the same real-world release (MusicBrainz's and Discogs'
records of it) stay as two separate, individually pickable rows —
flagged as duplicate alternatives, which is a visual hint only and
never changes what picking either one applies.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.matching.weights import ALBUM_REJECT_THRESHOLD as _ABSOLUTE_REJECT_DISTANCE
from muzilla.providers.base import CandidateTrack, MetadataProvider, ReleaseCandidate, ReleaseQuery
from muzilla.providers.cache import cache_get_fresh, cache_get_stale, cache_put, query_hash

logger = logging.getLogger(__name__)

SearchFn = Callable[[ReleaseQuery, int], Awaitable[list[ReleaseCandidate]]]


@dataclass(frozen=True, slots=True)
class ScoreSignal:
    """One visible scoring signal or adjustment.

    ``contribution`` is the normalized amount added to the raw distance.  A
    negative contribution is a bonus (for example corroboration), making the
    final score inspectable without exposing implementation-only fuzzy values.
    """

    field: str
    distance: float
    weight: float
    contribution: float


@dataclass(frozen=True, slots=True)
class CandidateScore:
    distance: float
    signals: tuple[ScoreSignal, ...] = ()
    representative_track: CandidateTrack | None = None
    related: bool = True
    rejection_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderSearchOutcome:
    provider: str
    status: Literal["results", "zero_results", "failed", "not_configured"]
    result_count: int = 0
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    candidates: tuple[ReleaseCandidate, ...]
    provider_outcomes: tuple[ProviderSearchOutcome, ...]


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    candidate: ReleaseCandidate
    distance: float
    """Raw per-candidate distance before corroboration/priority adjustment."""
    adjusted_distance: float
    """Distance after corroboration bonus and source-priority penalty —
    what ranking actually sorts by."""
    is_duplicate_of: tuple[int, ...] = ()
    """Indices (into the returned ranked list) of other candidates this
    one is flagged as a duplicate-alternative of. Symmetric: if A lists
    B, B lists A. The value is a UI hint and never merges candidate data."""
    corroborated_by: tuple[str, ...] = ()
    """Other provider names whose candidate was judged the same release."""
    signals: tuple[ScoreSignal, ...] = ()
    representative_track: CandidateTrack | None = None
    rejected: bool = False
    rejection_reason: str | None = None


async def gather_candidates(
    query: ReleaseQuery,
    searchers: Mapping[str, SearchFn],
    limit_per_provider: int = 5,
) -> list[ReleaseCandidate]:
    """Query every enabled provider in parallel; a dead provider never
    fails the whole match — its exception is logged and it's simply
    absent from the pool.
    """
    names = list(searchers.keys())
    results = await asyncio.gather(
        *(searchers[name](query, limit_per_provider) for name in names),
        return_exceptions=True,
    )
    candidates: list[ReleaseCandidate] = []
    for name, result in zip(names, results, strict=True):
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                logger.warning("provider %s failed during candidate gathering: %s", name, result)
                continue
            raise result
        candidates.extend(result)
    return candidates


def _dedupe_summaries(candidates: list[ReleaseCandidate]) -> list[ReleaseCandidate]:
    """Remove repeated provider references before spending hydrate budget.

    Different providers remain separate alternatives: cross-provider identity is
    represented later as duplicate provenance, rather than silently choosing a
    source and losing its metadata.
    """
    seen: set[tuple[str, str]] = set()
    deduped: list[ReleaseCandidate] = []
    for candidate in candidates:
        key = (candidate.source, candidate.ref.id)
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


def _candidate_to_dict(candidate: ReleaseCandidate) -> dict[str, object]:
    return {
        "source": candidate.source,
        "ref": {"provider": candidate.ref.provider, "id": candidate.ref.id},
        "album": candidate.album,
        "album_artist": candidate.album_artist,
        "year": candidate.year,
        "original_year": candidate.original_year,
        "label": candidate.label,
        "catalog_number": candidate.catalog_number,
        "barcode": candidate.barcode,
        "country": candidate.country,
        "media": candidate.media,
        "is_compilation": candidate.is_compilation,
        "track_count": candidate.track_count,
        "tracks": [asdict(t) for t in candidate.tracks],
        "candidate_type": candidate.candidate_type,
        "representative_track": asdict(candidate.representative_track) if candidate.representative_track else None,
        "mb_release_id": candidate.mb_release_id,
        "mb_release_group_id": candidate.mb_release_group_id,
        "discogs_release_id": candidate.discogs_release_id,
        "deezer_album_id": candidate.deezer_album_id,
        "external_ids": dict(candidate.external_ids),
        "art_refs": [asdict(a) for a in candidate.art_refs],
        "raw": dict(candidate.raw),
    }


def _dict_to_candidate(data: Any) -> ReleaseCandidate:
    from muzilla.providers.base import ArtRef, ProviderRef

    if not isinstance(data, dict):
        data = {}
    ref_data = data.get("ref", {})
    if not isinstance(ref_data, dict):
        ref_data = {}
    ref = ProviderRef(
        provider=str(ref_data.get("provider", "")), id=str(ref_data.get("id", ""))
    )
    tracks_data = data.get("tracks", [])
    tracks = tuple(
        CandidateTrack(
            position=int(t.get("position", 0)),
            title=str(t.get("title", "")),
            artist=t.get("artist"),
            duration_ms=t.get("duration_ms"),
            disc_number=t.get("disc_number"),
            isrc=t.get("isrc"),
            mb_track_id=t.get("mb_track_id"),
            mb_recording_id=t.get("mb_recording_id"),
        )
        for t in tracks_data
        if isinstance(t, dict)
    )
    art_refs_data = data.get("art_refs", [])
    art_refs = tuple(
        ArtRef(
            url=str(a.get("url", "")),
            source=str(a.get("source", "")),
            width=a.get("width"),
            height=a.get("height"),
            mime=a.get("mime"),
        )
        for a in art_refs_data
        if isinstance(a, dict)
    )
    rep_data = data.get("representative_track")
    representative = None
    if isinstance(rep_data, dict):
        representative = CandidateTrack(
            position=int(rep_data.get("position", 0)),
            title=str(rep_data.get("title", "")),
            artist=rep_data.get("artist"),
            duration_ms=rep_data.get("duration_ms"),
            disc_number=rep_data.get("disc_number"),
            isrc=rep_data.get("isrc"),
            mb_track_id=rep_data.get("mb_track_id"),
            mb_recording_id=rep_data.get("mb_recording_id"),
        )
    return ReleaseCandidate(
        source=str(data.get("source", "")),
        ref=ref,
        album=data.get("album"),
        album_artist=data.get("album_artist"),
        year=data.get("year"),
        original_year=data.get("original_year"),
        label=data.get("label"),
        catalog_number=data.get("catalog_number"),
        barcode=data.get("barcode"),
        country=data.get("country"),
        media=data.get("media"),
        is_compilation=bool(data.get("is_compilation", False)),
        track_count=data.get("track_count"),
        tracks=tracks,
        candidate_type=str(data.get("candidate_type", "release")),
        representative_track=representative,
        mb_release_id=data.get("mb_release_id"),
        mb_release_group_id=data.get("mb_release_group_id"),
        discogs_release_id=data.get("discogs_release_id"),
        deezer_album_id=data.get("deezer_album_id"),
        external_ids=dict(data.get("external_ids", {}) if isinstance(data.get("external_ids", {}), dict) else {}),
        art_refs=art_refs,
        raw=dict(data.get("raw", {}) if isinstance(data.get("raw", {}), dict) else {}),
    )


def _search_cache_key(provider: str, query: ReleaseQuery | None, limit: int) -> str:
    if query is None:
        return query_hash(provider, "search_releases", None, limit)
    return query_hash(
        provider,
        "search_releases",
        query.album,
        query.album_artist,
        query.artist,
        query.title,
        query.track_count,
        query.year,
        query.barcode,
        query.catalog_number,
        query.isrc,
        query.duration_ms,
        limit,
    )


def _hydrate_cache_key(provider: str, ref_id: str) -> str:
    return query_hash(provider, "get_release", ref_id)


async def retrieve_and_hydrate(
    query: ReleaseQuery,
    providers: Mapping[str, MetadataProvider],
    *,
    search_limit: int = 12,
    hydrate_limit: int = 8,
    hydrate_concurrency: int = 4,
    session: Session | None = None,
    config: Config | None = None,
    refresh: bool = False,
) -> RetrievalResult:
    """Retrieve summaries, then hydrate a bounded shortlist before ranking.

    Search failures deliberately become a typed outcome; they must never look
    like an empty provider result.  Hydration is bounded independently of the
    wider retrieval limit, so a noisy provider cannot turn a single match into
    unbounded network work.

    When a session/config is provided, results are routed through the persistent
    semantic cache with versioned entries, TTL, manual refresh, and stale fallback.
    Offline mode (config.providers_offline) is network-free and returns only cached data.
    """
    names = list(providers)
    is_offline = bool(config and getattr(config, "providers_offline", False))
    summaries_by_provider: dict[str, list[ReleaseCandidate]] = {}
    outcomes: dict[str, ProviderSearchOutcome] = {}

    async def _search_one(name: str) -> tuple[str, list[ReleaseCandidate] | None, Exception | None, bool, bool]:
        # Returns (name, candidates_or_none, error, from_cache, stale)
        provider = providers[name]
        cache_key = _search_cache_key(name, query, search_limit)
        # Try fresh cache unless refresh or offline with no fresh
        if session is not None and not refresh and not is_offline:
            fresh = cache_get_fresh(session, name, "search_releases", cache_key)
            if fresh is not None and isinstance(fresh, list):
                try:
                    candidates = [_dict_to_candidate(d) for d in fresh if isinstance(d, dict)]
                    return name, candidates, None, True, False
                except Exception:
                    pass
        if is_offline:
            # Offline: only cached data, no network. Try stale as well.
            if session is not None:
                stale = cache_get_stale(session, name, "search_releases", cache_key)
                if stale is not None and isinstance(stale, list):
                    try:
                        candidates = [_dict_to_candidate(d) for d in stale if isinstance(d, dict)]
                        return name, candidates, None, True, True
                    except Exception:
                        pass
            return name, [], None, False, False
        # Network path (unless refresh bypasses fresh but still tries network)
        try:
            result = await provider.search_releases(query, search_limit)
            # Cache on success
            if session is not None:
                try:
                    payload = [_candidate_to_dict(c) for c in result]
                    cache_put(session, name, "search_releases", cache_key, payload)
                    session.flush()
                except Exception:
                    pass
            return name, result, None, False, False
        except Exception as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.warning("provider %s failed during candidate retrieval: %s", name, exc)
            # Fallback to stale on failure
            if session is not None:
                stale = cache_get_stale(session, name, "search_releases", cache_key)
                if stale is not None and isinstance(stale, list):
                    try:
                        candidates = [_dict_to_candidate(d) for d in stale if isinstance(d, dict)]
                        return name, candidates, None, True, True
                    except Exception:
                        pass
            return name, None, exc, False, False

    search_results = await asyncio.gather(*(_search_one(name) for name in names))
    for name, result, error, from_cache, stale in search_results:
        if error is not None:
            outcomes[name] = ProviderSearchOutcome(name, "failed", detail="search failed" + (" (stale cache fallback)" if from_cache and stale else ""))
            continue
        if result is None or len(result) == 0:
            # Distinguish between genuine zero results vs offline with no cache (also zero)
            outcomes[name] = ProviderSearchOutcome(name, "zero_results")
            continue
        detail = None
        if from_cache:
            detail = "stale cache" if stale else "cache"
            if is_offline:
                detail = "offline cache" + (" (stale)" if stale else "")
        outcomes[name] = ProviderSearchOutcome(name, "results", result_count=len(result), detail=detail)
        summaries_by_provider[name] = result

    # Interleave providers before cutting the shortlist so a single source's
    # broad recall cannot consume the entire hydrate budget.
    summaries: list[ReleaseCandidate] = []
    seen_summary_refs: set[tuple[str, str]] = set()
    summary_index = 0
    while len(summaries) < hydrate_limit:
        added = False
        for name in names:
            provider_summaries = summaries_by_provider.get(name, [])
            if summary_index < len(provider_summaries):
                candidate = provider_summaries[summary_index]
                key = (candidate.source, candidate.ref.id)
                if key not in seen_summary_refs:
                    seen_summary_refs.add(key)
                    summaries.append(candidate)
                added = True
                if len(summaries) >= hydrate_limit:
                    break
        if not added:
            break
        summary_index += 1
    shortlist = _dedupe_summaries(summaries)
    semaphore = asyncio.Semaphore(hydrate_concurrency)

    async def hydrate(
        summary: ReleaseCandidate,
    ) -> tuple[ReleaseCandidate, ReleaseCandidate | None, Exception | None, bool, bool]:
        # Returns (summary, full_or_none, error, from_cache, stale)
        cache_key = _hydrate_cache_key(summary.source, summary.ref.id)
        if session is not None and not refresh and not is_offline:
            fresh = cache_get_fresh(session, summary.source, "get_release", cache_key)
            if fresh is not None and isinstance(fresh, dict):
                try:
                    cand = _dict_to_candidate(fresh)
                    return summary, cand, None, True, False
                except Exception:
                    pass
        if is_offline:
            if session is not None:
                stale = cache_get_stale(session, summary.source, "get_release", cache_key)
                if stale is not None and isinstance(stale, dict):
                    try:
                        cand = _dict_to_candidate(stale)
                        return summary, cand, None, True, True
                    except Exception:
                        pass
            return summary, None, None, False, False
        try:
            async with semaphore:
                full = await providers[summary.source].get_release(summary.ref)
            if full is not None and session is not None:
                try:
                    cache_put(session, summary.source, "get_release", cache_key, _candidate_to_dict(full))
                    session.flush()
                except Exception:
                    pass
            return summary, full, None, False, False
        except Exception as exc:  # provider boundary; surfaced as a typed outcome
            if session is not None:
                stale = cache_get_stale(session, summary.source, "get_release", cache_key)
                if stale is not None and isinstance(stale, dict):
                    try:
                        cand = _dict_to_candidate(stale)
                        return summary, cand, None, True, True
                    except Exception:
                        pass
            return summary, None, exc, False, False

    hydrated = await asyncio.gather(*(hydrate(summary) for summary in shortlist))
    final: list[ReleaseCandidate] = []
    hydration_failures: set[str] = set()
    for summary, full, error, _from_cache, _stale in hydrated:
        if error is not None:
            logger.warning(
                "provider %s failed during candidate hydration: %s", summary.source, error
            )
            hydration_failures.add(summary.source)
            continue
        if full is None:
            continue
        final.append(
            replace(
                full,
                candidate_type=summary.candidate_type,
                representative_track=summary.representative_track,
                track_count=full.track_count
                if full.track_count is not None
                else summary.track_count,
            )
        )

    for name in hydration_failures:
        if not any(candidate.source == name for candidate in final):
            outcomes[name] = ProviderSearchOutcome(name, "failed", detail="hydrate failed")

    return RetrievalResult(
        candidates=tuple(_dedupe_summaries(final)),
        provider_outcomes=tuple(outcomes[name] for name in names),
    )


def _is_duplicate_pair(
    a: ReleaseCandidate,
    b: ReleaseCandidate,
    score_fn: Callable[[ReleaseCandidate, ReleaseCandidate], float],
) -> bool:
    """Same-release heuristic: shared barcode, shared
    MBID (Discogs often carries MB links via external_ids), or close
    artist+album distance with matching track count and |year| <= 1.
    """
    if a.source == b.source:
        return False
    if a.barcode and b.barcode and a.barcode == b.barcode:
        return True
    a_mbid = a.mb_release_id or a.external_ids.get("musicbrainz")
    b_mbid = b.mb_release_id or b.external_ids.get("musicbrainz")
    if a_mbid and b_mbid and a_mbid == b_mbid:
        return True
    if len(a.tracks) != len(b.tracks) or not a.tracks:
        return False
    if a.year is not None and b.year is not None and abs(a.year - b.year) > 1:
        return False
    return score_fn(a, b) < 0.10


def rank_candidates(
    candidates: list[ReleaseCandidate],
    score_fn: Callable[[ReleaseCandidate], float | CandidateScore],
    dup_score_fn: Callable[[ReleaseCandidate, ReleaseCandidate], float],
    source_priority: tuple[str, ...] = (),
    source_penalty: float = 0.0,
    corroboration_bonus: float = 0.05,
) -> list[ScoredCandidate]:
    """Score, flag duplicates, apply corroboration, and sort.

    `score_fn` scores one candidate against the local group/track (the
    caller's job — album vs. singleton scoring differ, see engine.py).
    `dup_score_fn` scores two *candidates* against each other for the
    duplicate-alternative heuristic — distinct from `score_fn` because
    it compares release-to-release, not release-to-local.
    """
    scored: list[tuple[ReleaseCandidate, CandidateScore]] = []
    for candidate in candidates:
        score = score_fn(candidate)
        scored.append(
            (candidate, score if isinstance(score, CandidateScore) else CandidateScore(score))
        )

    # Duplicate-alternative flagging: symmetric adjacency by index.
    dup_groups: list[list[int]] = [[] for _ in scored]
    for i, (a, _) in enumerate(scored):
        for j, (b, _) in enumerate(scored):
            if i != j and _is_duplicate_pair(a, b, dup_score_fn):
                dup_groups[i].append(j)

    results: list[ScoredCandidate] = []
    for i, (candidate, score) in enumerate(scored):
        corroborators = {
            scored[j][0].source for j in dup_groups[i] if scored[j][0].source != candidate.source
        }
        adjusted = score.distance - corroboration_bonus * len(corroborators)
        signals = list(score.signals)
        if corroborators:
            signals.append(
                ScoreSignal(
                    field="corroboration",
                    distance=0.0,
                    weight=float(len(corroborators)),
                    contribution=-corroboration_bonus * len(corroborators),
                )
            )
        if source_priority and candidate.source in source_priority:
            rank = source_priority.index(candidate.source)
            adjusted += source_penalty * rank
            if rank:
                signals.append(
                    ScoreSignal(
                        field="source_priority",
                        distance=1.0,
                        weight=float(rank),
                        contribution=source_penalty * rank,
                    )
                )
        adjusted = max(0.0, min(1.0, adjusted))
        rejection_reason = score.rejection_reason
        if rejection_reason is None and not score.related:
            rejection_reason = "insufficient identity signals"
        if rejection_reason is None and adjusted >= _ABSOLUTE_REJECT_DISTANCE:
            rejection_reason = "candidate is not sufficiently related"
        results.append(
            ScoredCandidate(
                candidate=candidate,
                distance=score.distance,
                adjusted_distance=adjusted,
                is_duplicate_of=tuple(dup_groups[i]),
                corroborated_by=tuple(sorted(corroborators)),
                signals=tuple(signals),
                representative_track=score.representative_track or candidate.representative_track,
                rejected=rejection_reason is not None,
                rejection_reason=rejection_reason,
            )
        )

    order = sorted(range(len(results)), key=lambda i: results[i].adjusted_distance)
    reindex = {old: new for new, old in enumerate(order)}
    reordered = [results[i] for i in order]
    return [
        replace(r, is_duplicate_of=tuple(sorted(reindex[j] for j in r.is_duplicate_of)))
        for r in reordered
    ]
