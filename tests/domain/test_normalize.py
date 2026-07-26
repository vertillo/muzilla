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


def test_string_dist_remastered_suffix_is_close() -> None:
    # Not stripped explicitly (that's the curated-noise-regex step,
    # Phase 3 scope) but token_set_ratio still scores these close.
    assert string_dist("Abbey Road", "Abbey Road (Remastered)") < 0.3
