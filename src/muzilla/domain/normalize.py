"""String normalization + distance for fuzzy matching and grouping.

Pure, network-free. This is the full docs/product-spec.md `string_dist.py`
spec: unicode fold, article stripping, punctuation normalization,
`feat.` canonicalization, curated noise-regex bracket stripping, and
roman-numeral <-> digit unification. Shared by `pipeline/grouping.py`
(Stage 3 tag clustering) and `matching/` (Phase 3's release/recording
scoring) so both consumers use one normalization pipeline rather than
drifting into two slightly-different fuzzy matchers.
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

# Curated noise patterns stripped from bracketed content. Deliberately
# NOT a blanket "(...)" strip — "(Live at Budokan)" or "(Deluxe Edition
# Bonus Track)" are meaningful and must survive. Only remaster/edition/
# explicit-tag noise that never carries matching signal is removed.
_NOISE_BRACKET_RE = re.compile(
    r"[\(\[]\s*(?:"
    r"remaster(?:ed)?(?:\s*\d{0,4})?"
    r"|explicit"
    r"|clean"
    r"|bonus\s*track"
    r"|digital\s*(?:remaster|version)"
    r"|\d{4}\s*remaster(?:ed)?"
    r")\s*[\)\]]",
    re.IGNORECASE,
)

# Deliberately NOT a bare word-boundary match on any roman-looking
# token: "Mix" (M + IX), "Civic" (C + IV + I + C), and "Live" (L + IV +
# E — well, "Liv" alone is L+IV) all happen to be spellable from roman-
# numeral letters, so matching them anywhere would silently corrupt
# ordinary English words. Instead this only fires directly after a
# recognized ordinal/part marker ("Pt.", "Part", "Vol.", "No.", "Op.",
# "Disc", "CD", "Book", "Movement") — the actual classical/prog use
# case ("Pt. II" -> "Pt. 2") without touching unrelated text.
_ROMAN_MARKER_RE = re.compile(
    r"\b(pt\.?|part|vol\.?|volume|no\.?|op\.?|disc|cd|book|movement)\s+"
    r"(M{1,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})|(IX|IV|V?I{1,3}))\b",
    re.IGNORECASE,
)
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _roman_to_int(s: str) -> int:
    total = 0
    prev = 0
    for ch in reversed(s.upper()):
        val = _ROMAN_VALUES[ch]
        total += -val if val < prev else val
        prev = max(prev, val)
    return total


def _unify_roman_numerals(value: str) -> str:
    """Replace a roman numeral directly after an ordinal marker with its
    digit equivalent: "Pt. II" -> "Pt. 2", "Part III" -> "Part 3".

    Matters for classical and prog rock where movement/part numbering
    flips between the two conventions across sources. Deliberately
    scoped to right after a marker word rather than any roman-looking
    token in the string — "Mix", "Civic", "Live" are all spellable
    from roman-numeral letters and must not be touched.
    """

    def _sub(m: re.Match[str]) -> str:
        marker, numeral = m.group(1), m.group(2)
        return f"{marker} {_roman_to_int(numeral)}"

    return _ROMAN_MARKER_RE.sub(_sub, value)


def normalize_for_match(value: str | None) -> str:
    """Normalize a string for fuzzy comparison:

    1. NFKD decompose, strip combining marks, casefold
    2. Strip curated noise brackets (remaster/explicit/etc — never a
       blanket bracket strip)
    3. Strip a single leading article (the/a/an)
    4. Canonicalize feat./ft./featuring/with -> "feat"
    5. Roman numeral -> digit unification
    6. Strip punctuation, collapse whitespace
    """
    if not value:
        return ""

    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = stripped.casefold().strip()

    folded = _NOISE_BRACKET_RE.sub(" ", folded)

    for article in _LEADING_ARTICLES:
        if folded.startswith(article):
            folded = folded[len(article) :]
            break

    folded = _FEAT_RE.sub("feat", folded)
    folded = _unify_roman_numerals(folded)
    folded = _PUNCT_RE.sub(" ", folded)
    folded = _WHITESPACE_RE.sub(" ", folded).strip()
    return folded


def string_dist(a: str | None, b: str | None) -> float:
    """Normalized distance in [0, 1]; 0.0 = identical, 1.0 = unrelated.

    Uses token-sort and plain ratio.  Token-set ratio intentionally is
    not used here: it makes a repeated title such as ``Twilight Twilight``
    indistinguishable from ``Twilight`` and over-rewards subset matches.
    """
    na, nb = normalize_for_match(a), normalize_for_match(b)
    if na == nb:
        return 0.0
    if not na or not nb:
        return 1.0
    token_sorted = fuzz.token_sort_ratio(na, nb) / 100.0
    plain = fuzz.ratio(na, nb) / 100.0
    return 1.0 - max(token_sorted, plain)
