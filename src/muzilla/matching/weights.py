"""Weighted-field configuration for the two matching paths.

Album/release matching and singleton/recording matching are different
problems (docs/product-spec.md) — a release has a tracklist to align and
corroborating signals (track count, media, label) a lone recording
never has. Kept as plain dicts, user-overridable via `config/`, rather
than a frozen dataclass: matching/distance.py just needs a
`Mapping[str, float]`.
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

# Rank thresholds (docs/product-spec.md): distance below AUTO is auto-applicable
# in --quiet mode with no destructive changes; below CONFIRM needs human
# confirmation but is shown first; above is always human review.
ALBUM_AUTO_THRESHOLD = 0.10
ALBUM_CONFIRM_THRESHOLD = 0.25

# Singleton matching has fewer corroborating signals, so a confident-
# looking wrong match is easier to produce — auto-apply is stricter.
SINGLETON_AUTO_THRESHOLD = 0.06

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
