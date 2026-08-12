"""Track-to-track alignment between a local group and a candidate release.

docs/product-spec.md: beets' greedy sequential comparison breaks on
out-of-order rips, hidden tracks, and bonus discs. Instead: build a
cost matrix and solve it optimally with the Hungarian algorithm
(`scipy.optimize.linear_sum_assignment`), which is trivially fast at
album scale (a few dozen tracks).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from muzilla.matching.weights import MISSING_COST, SEQUENTIAL_PRIOR_WEIGHT


@dataclass(frozen=True, slots=True)
class TrackAlignment:
    local_index: int | None
    """Index into the local track list, or None if this candidate track
    has no local counterpart (missing from the rip)."""
    candidate_index: int | None
    """Index into the candidate release's tracklist, or None if this
    local track matches nothing on the candidate (unmatched extra)."""
    cost: float
    """The pairwise distance for a real pairing; MISSING_COST for a
    dummy pairing."""


def align_tracks[L, C](
    local: Sequence[L],
    candidate: Sequence[C],
    pair_distance: Callable[[L, C], float],
    disc_of: Callable[[L], int | None] | None = None,
) -> list[TrackAlignment]:
    """Align local tracks against a candidate release's tracklist.

    `pair_distance(local_track, candidate_track) -> float in [0, 1]`.
    `disc_of`, if given, is applied to *local* tracks; when disc numbers
    are present and reliable, alignment is solved independently per
    disc (docs/product-spec.md's disc-aware refinement) rather than across the
    whole release, which both keeps the cost matrix small and avoids
    cross-disc mismatches on multi-disc sets.
    """
    if not local and not candidate:
        return []

    if disc_of is not None:
        discs: dict[int | None, list[int]] = {}
        for i, t in enumerate(local):
            discs.setdefault(disc_of(t), []).append(i)
        if len(discs) > 1 and all(d is not None for d in discs):
            # Only worth splitting when every local track has a disc
            # number — a partially-tagged set can't be reliably split.
            results: list[TrackAlignment] = []
            # Candidate tracks are distributed evenly across discs in
            # tracklist order as a best-effort split; real releases
            # rarely need this since candidate data usually carries its
            # own disc numbers, which callers can pre-slice before
            # calling this function per disc instead.
            for local_indices in discs.values():
                sub_local = [local[i] for i in local_indices]
                sub = _align_flat(sub_local, candidate, pair_distance)
                for a in sub:
                    results.append(
                        TrackAlignment(
                            local_index=local_indices[a.local_index]
                            if a.local_index is not None
                            else None,
                            candidate_index=a.candidate_index,
                            cost=a.cost,
                        )
                    )
            return results

    return _align_flat(local, candidate, pair_distance)


def _align_flat[L, C](
    local: Sequence[L],
    candidate: Sequence[C],
    pair_distance: Callable[[L, C], float],
) -> list[TrackAlignment]:
    n, m = len(local), len(candidate)
    size = max(n, m)
    if size == 0:
        return []

    cost = np.full((size, size), MISSING_COST, dtype=float)
    denom = max(n, m, 1)
    for i in range(n):
        for j in range(m):
            base = pair_distance(local[i], candidate[j])
            prior = SEQUENTIAL_PRIOR_WEIGHT * abs(i - j) / denom
            cost[i, j] = min(base + prior, 1.0)

    row_ind, col_ind = linear_sum_assignment(cost)

    alignments: list[TrackAlignment] = []
    for r, c in zip(row_ind, col_ind, strict=True):
        r, c = int(r), int(c)
        local_idx = r if r < n else None
        cand_idx = c if c < m else None
        if local_idx is None and cand_idx is None:
            continue
        alignments.append(
            TrackAlignment(local_index=local_idx, candidate_index=cand_idx, cost=float(cost[r, c]))
        )
    return alignments
