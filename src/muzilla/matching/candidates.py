"""Multi-source candidate gathering — one release, one source
(docs/product-spec.md).

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
from dataclasses import dataclass, replace
from typing import Literal

from muzilla.providers.base import CandidateTrack, MetadataProvider, ReleaseCandidate, ReleaseQuery

logger = logging.getLogger(__name__)

SearchFn = Callable[[ReleaseQuery, int], Awaitable[list[ReleaseCandidate]]]

_ABSOLUTE_REJECT_DISTANCE = 0.45


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
    B, B lists A. Never used to merge data, only to render a UI hint."""
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


async def retrieve_and_hydrate(
    query: ReleaseQuery,
    providers: Mapping[str, MetadataProvider],
    *,
    search_limit: int = 12,
    hydrate_limit: int = 8,
    hydrate_concurrency: int = 4,
) -> RetrievalResult:
    """Retrieve summaries, then hydrate a bounded shortlist before ranking.

    Search failures deliberately become a typed outcome; they must never look
    like an empty provider result.  Hydration is bounded independently of the
    wider retrieval limit, so a noisy provider cannot turn a single match into
    unbounded network work.
    """
    names = list(providers)
    search_results = await asyncio.gather(
        *(providers[name].search_releases(query, search_limit) for name in names),
        return_exceptions=True,
    )
    summaries_by_provider: dict[str, list[ReleaseCandidate]] = {}
    outcomes: dict[str, ProviderSearchOutcome] = {}
    for name, result in zip(names, search_results, strict=True):
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                logger.warning("provider %s failed during candidate retrieval: %s", name, result)
                outcomes[name] = ProviderSearchOutcome(name, "failed", detail="search failed")
                continue
            raise result
        if not result:
            outcomes[name] = ProviderSearchOutcome(name, "zero_results")
            continue
        outcomes[name] = ProviderSearchOutcome(name, "results", result_count=len(result))
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

    async def hydrate(summary: ReleaseCandidate) -> tuple[ReleaseCandidate, ReleaseCandidate | None, Exception | None]:
        try:
            async with semaphore:
                full = await providers[summary.source].get_release(summary.ref)
            return summary, full, None
        except Exception as exc:  # provider boundary; surfaced as a typed outcome
            return summary, None, exc

    hydrated = await asyncio.gather(*(hydrate(summary) for summary in shortlist))
    final: list[ReleaseCandidate] = []
    hydration_failures: set[str] = set()
    for summary, full, error in hydrated:
        if error is not None:
            logger.warning("provider %s failed during candidate hydration: %s", summary.source, error)
            hydration_failures.add(summary.source)
            continue
        if full is None:
            continue
        final.append(
            replace(
                full,
                candidate_type=summary.candidate_type,
                representative_track=summary.representative_track,
                track_count=full.track_count if full.track_count is not None else summary.track_count,
            )
        )

    for name in hydration_failures:
        if not any(candidate.source == name for candidate in final):
            outcomes[name] = ProviderSearchOutcome(name, "failed", detail="hydrate failed")

    return RetrievalResult(
        candidates=tuple(_dedupe_summaries(final)),
        provider_outcomes=tuple(outcomes[name] for name in names),
    )


def _is_duplicate_pair(a: ReleaseCandidate, b: ReleaseCandidate, score_fn: Callable[[ReleaseCandidate, ReleaseCandidate], float]) -> bool:
    """Same-release heuristic (docs/product-spec.md): shared barcode, shared
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
        scored.append((candidate, score if isinstance(score, CandidateScore) else CandidateScore(score)))

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
