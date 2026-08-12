"""Weighted distance accumulator — the scoring primitive shared by every
match path.

`Σ(w·d)/Σw`, normalized to [0, 1]. Deliberately simple: callers compute
per-field distances however makes sense for that field (string_dist for
text, exact-mismatch for ids, numeric closeness for year/length) and
hand this module a `{field: distance}` mapping plus the weight table.
Keeping the accumulator itself dumb is what makes weights tunable
without touching scoring logic.
"""

from __future__ import annotations

from collections.abc import Mapping

from muzilla.matching.candidates import ScoreSignal


def weighted_distance(
    field_distances: Mapping[str, float],
    weights: Mapping[str, float],
) -> float:
    """Combine per-field distances into one normalized score.

    Fields absent from `field_distances` (no signal available — e.g. no
    barcode on either side) simply don't contribute; their weight is
    excluded from the denominator so missing data doesn't silently drag
    the score toward "identical".
    """
    total_weight = 0.0
    total = 0.0
    for field, dist in field_distances.items():
        w = weights.get(field)
        if w is None or w <= 0.0:
            continue
        total += w * dist
        total_weight += w
    if total_weight == 0.0:
        return 1.0
    return total / total_weight


def explained_weighted_distance(
    field_distances: Mapping[str, float], weights: Mapping[str, float]
) -> tuple[float, tuple[ScoreSignal, ...]]:
    """Return a score together with each field's normalized contribution."""
    score = weighted_distance(field_distances, weights)
    total_weight = sum(
        weights.get(field, 0.0)
        for field in field_distances
        if weights.get(field, 0.0) > 0.0
    )
    if total_weight == 0.0:
        return score, ()
    return score, tuple(
        ScoreSignal(
            field=field,
            distance=distance,
            weight=weights[field],
            contribution=weights[field] * distance / total_weight,
        )
        for field, distance in field_distances.items()
        if weights.get(field, 0.0) > 0.0
    )


def numeric_distance(a: float | None, b: float | None, scale: float) -> float:
    """Distance in [0, 1] for a numeric field (year, duration, track count).

    `scale` is the difference at which distance saturates to 1.0 — e.g.
    a `scale` of 2 for `year` means a 2-year difference is already
    "unrelated", while a `scale` of 10_000 (ms) for duration allows a
    10s tolerance before fully penalizing.
    """
    if a is None or b is None:
        return 1.0
    return min(abs(a - b) / scale, 1.0) if scale > 0 else (0.0 if a == b else 1.0)


def exact_distance(a: object | None, b: object | None) -> float:
    """0.0 if both present and equal, 1.0 otherwise (including either missing).

    For identifiers (barcode, MBID, ISRC) where partial credit makes no
    sense — either it's the same release or it isn't.
    """
    if a is None or b is None:
        return 1.0
    return 0.0 if a == b else 1.0
