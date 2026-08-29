"""Fixed weighting tables for album/release and singleton matching.

Album matching can use tracklist and release evidence; singleton matching
has fewer corroborating signals and therefore uses its own weights.
"""

from __future__ import annotations

ALBUM_WEIGHTS: dict[str, float] = {
    "album": 3.0,
    "album_artist": 3.0,
    "tracks": 2.0,
    "missing_tracks": 0.9,
    "unmatched_tracks": 0.6,
    "year": 0.5,
    "media": 0.5,
    "country": 0.5,
    "label": 0.5,
    "catalog_number": 0.5,
    "album_id": 5.0,
    "barcode": 2.0,
    "source": 2.0,
}

TRACK_WEIGHTS: dict[str, float] = {
    "title": 3.0,
    "artist": 2.0,
    "length": 2.0,
    "index": 1.0,
    "track_id": 5.0,
    "isrc": 4.0,
}

SINGLETON_WEIGHTS: dict[str, float] = {
    "title": 3.0,
    "artist": 3.0,
    "length": 2.5,
    "isrc": 4.0,
    "acoustid": 5.0,
}

# Settled matching bands: strong / ambiguous / reject.
# distance < STRONG  -> strong (may preselect, still requires explicit Apply)
# STRONG <= distance < REJECT and related -> ambiguous (ordered list, requires explicit selection or Skip)
# otherwise -> reject (never shown, never selectable)
ALBUM_STRONG_THRESHOLD = 0.10
ALBUM_REJECT_THRESHOLD = 0.45
SINGLETON_STRONG_THRESHOLD = 0.06
SINGLETON_REJECT_THRESHOLD = ALBUM_REJECT_THRESHOLD
# Legacy aliases — kept for compatibility, do not use in new code.
ALBUM_AUTO_THRESHOLD = ALBUM_STRONG_THRESHOLD
ALBUM_CONFIRM_THRESHOLD = ALBUM_REJECT_THRESHOLD
SINGLETON_AUTO_THRESHOLD = SINGLETON_STRONG_THRESHOLD
ALBUM_AMBIGUOUS_THRESHOLD = ALBUM_REJECT_THRESHOLD
SINGLETON_AMBIGUOUS_THRESHOLD = SINGLETON_REJECT_THRESHOLD

# Distance bonus per independent source corroborating the same release
# (see matching/candidates.py) and penalty per step down the configured
# source_priority tie-breaker list.
CORROBORATION_BONUS_PER_SOURCE = 0.05
DEFAULT_SOURCE_PRIORITY: tuple[str, ...] = ("musicbrainz", "discogs", "deezer")
DEFAULT_SOURCE_PENALTY = 0.02

MISSING_COST = 0.85
"""Cost of a dummy row/column in Hungarian track alignment (an unmatched
local track or an unmatched candidate track)."""

SEQUENTIAL_PRIOR_WEIGHT = 0.15
"""Weight of `|i-j| / max(n, m)` added to the alignment cost matrix —
breaks ties toward natural order without forbidding reordering."""
