"""String normalization + distance for fuzzy matching and grouping.

Pure, network-free. This is a deliberately narrower slice of
docs/PLAN.md §3's full `string_dist.py` spec (roman numeral unification,
curated noise-regex bracket stripping, etc. are Phase-3 matching-engine
scope) — Phase 2 needs only the pipeline steps `pipeline/grouping.py`'s
Stage 3 tag clustering actually uses: unicode fold, article stripping,
punctuation normalization, and `feat.` canonicalization. Extend this
module in place when Phase 3 builds the full matching engine so both
consumers keep sharing one normalization pipeline rather than drifting.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

_LEADING_ARTICLES = ("the ", "a ", "an ")

_FEAT_RE = re.compile(
    r"\b(feat\.?|ft\.?|featuring|with)\b", re.IGNORECASE
)
_PUNCT_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_match(value: str | None) -> str:
    """Normalize a string for fuzzy comparison:

    1. NFKD decompose, strip combining marks, casefold
    2. Strip a single leading article (the/a/an)
    3. Canonicalize feat./ft./featuring/with -> "feat"
    4. Strip punctuation, collapse whitespace
    """
    if not value:
        return ""

    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = stripped.casefold().strip()

    for article in _LEADING_ARTICLES:
        if folded.startswith(article):
            folded = folded[len(article) :]
            break

    folded = _FEAT_RE.sub("feat", folded)
    folded = _PUNCT_RE.sub(" ", folded)
    folded = _WHITESPACE_RE.sub(" ", folded).strip()
    return folded


def string_dist(a: str | None, b: str | None) -> float:
    """Normalized distance in [0, 1]; 0.0 = identical, 1.0 = unrelated.

    Uses rapidfuzz's token_set_ratio and plain ratio, taking whichever
    scores the pair closer — token_set_ratio handles reordered/subset
    token matches ("Greatest Hits" vs "The Greatest Hits Vol. 1") while
    plain ratio catches near-identical strings token_set_ratio can
    over-forgive.
    """
    na, nb = normalize_for_match(a), normalize_for_match(b)
    if na == nb:
        return 0.0
    if not na or not nb:
        return 1.0
    token_set = fuzz.token_set_ratio(na, nb) / 100.0
    plain = fuzz.ratio(na, nb) / 100.0
    return 1.0 - max(token_set, plain)
