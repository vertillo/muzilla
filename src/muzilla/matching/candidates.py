"""Multi-source candidate gathering — one release, one source
(docs/PLAN.md §3).

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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from muzilla.providers.base import ReleaseCandidate, ReleaseQuery

logger = logging.getLogger(__name__)

SearchFn = Callable[[ReleaseQuery, int], Awaitable[list[ReleaseCandidate]]]


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


async def gather_candidates(
    query: ReleaseQuery,
    searchers: dict[str, SearchFn],
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
            logger.warning("provider %s failed during candidate gathering: %s", name, result)
            continue
        candidates.extend(result)
    return candidates


def _is_duplicate_pair(a: ReleaseCandidate, b: ReleaseCandidate, score_fn: Callable[[ReleaseCandidate, ReleaseCandidate], float]) -> bool:
    """Same-release heuristic (docs/PLAN.md §3): shared barcode, shared
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
    score_fn: Callable[[ReleaseCandidate], float],
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
    scored = [(c, score_fn(c)) for c in candidates]

    # Duplicate-alternative flagging: symmetric adjacency by index.
    dup_groups: list[list[int]] = [[] for _ in scored]
    for i, (a, _) in enumerate(scored):
        for j, (b, _) in enumerate(scored):
            if i != j and _is_duplicate_pair(a, b, dup_score_fn):
                dup_groups[i].append(j)

    results: list[ScoredCandidate] = []
    for i, (candidate, distance) in enumerate(scored):
        corroborators = {
            scored[j][0].source for j in dup_groups[i] if scored[j][0].source != candidate.source
        }
        adjusted = distance - corroboration_bonus * len(corroborators)
        if source_priority and candidate.source in source_priority:
            rank = source_priority.index(candidate.source)
            adjusted += source_penalty * rank
        adjusted = max(0.0, min(1.0, adjusted))
        results.append(
            ScoredCandidate(
                candidate=candidate,
                distance=distance,
                adjusted_distance=adjusted,
                is_duplicate_of=tuple(dup_groups[i]),
                corroborated_by=tuple(sorted(corroborators)),
            )
        )

    order = sorted(range(len(results)), key=lambda i: results[i].adjusted_distance)
    reindex = {old: new for new, old in enumerate(order)}
    reordered = [results[i] for i in order]
    return [
        replace(r, is_duplicate_of=tuple(sorted(reindex[j] for j in r.is_duplicate_of)))
        for r in reordered
    ]
