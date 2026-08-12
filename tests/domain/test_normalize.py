from __future__ import annotations

from muzilla.domain.normalize import normalize_for_match, string_dist


def test_normalize_strips_leading_article() -> None:
    assert normalize_for_match("The Beatles") == normalize_for_match("Beatles")


def test_normalize_folds_diacritics() -> None:
    assert normalize_for_match("Sigur Rós") == normalize_for_match("Sigur Ros")


def test_normalize_canonicalizes_feat() -> None:
    a = normalize_for_match("Song (feat. Someone)")
    b = normalize_for_match("Song featuring Someone")
    assert a == b


def test_normalize_strips_punctuation_and_collapses_whitespace() -> None:
    assert normalize_for_match("Abbey  Road!!") == normalize_for_match("Abbey Road")


def test_string_dist_identical_strings_is_zero() -> None:
    assert string_dist("Abbey Road", "Abbey Road") == 0.0


def test_string_dist_near_identical_after_normalization_is_zero() -> None:
    assert string_dist("The Beatles", "Beatles") == 0.0
    assert string_dist("Sigur Ros", "Sigur Rós") == 0.0


def test_string_dist_unrelated_strings_is_high() -> None:
    assert string_dist("Abbey Road", "Definitely Maybe") > 0.5


def test_string_dist_empty_vs_nonempty_is_max() -> None:
    assert string_dist("", "Something") == 1.0
    assert string_dist(None, "Something") == 1.0


def test_string_dist_remastered_suffix_is_exact() -> None:
    # Curated noise-regex bracket stripping: remaster/edition
    # noise carries no matching signal and is fully removed.
    assert string_dist("Abbey Road", "Abbey Road (Remastered)") == 0.0
    assert string_dist("Abbey Road", "Abbey Road (Remastered 2009)") == 0.0
    assert string_dist("Song", "Song [Explicit]") == 0.0


def test_normalize_does_not_strip_meaningful_brackets() -> None:
    # Never a blanket bracket strip -- "(Live at Budokan)" is meaningful.
    assert normalize_for_match("Song (Live at Budokan)") != normalize_for_match("Song")


def test_normalize_unifies_roman_numerals() -> None:
    assert normalize_for_match("Part II") == normalize_for_match("Part 2")
    assert normalize_for_match("Pt. III") == normalize_for_match("Pt. 3")
    assert normalize_for_match("Symphony No. IX") == normalize_for_match("Symphony No. 9")


def test_normalize_does_not_corrupt_roman_looking_english_words() -> None:
    # "Mix", "Civic", "Live" are all spellable from roman-numeral
    # letters (M/D/C/L/X/V/I) -- must never be converted to digits.
    assert normalize_for_match("Mix") == "mix"
    assert normalize_for_match("Civic") == "civic"
    assert normalize_for_match("Live") == "live"
