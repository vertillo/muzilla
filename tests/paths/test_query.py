from __future__ import annotations

import pytest

from muzilla.paths.query import matches, parse_query


def test_single_clause() -> None:
    q = parse_query("genre:Classical")
    assert q.clauses == (("genre", "Classical"),)


def test_multi_clause_anded() -> None:
    q = parse_query("genre:Classical,albumartist:Bach")
    assert q.clauses == (("genre", "Classical"), ("albumartist", "Bach"))


def test_strips_whitespace() -> None:
    q = parse_query(" genre : Classical , albumartist : Bach ")
    assert q.clauses == (("genre", "Classical"), ("albumartist", "Bach"))


def test_missing_colon_raises() -> None:
    with pytest.raises(ValueError, match="malformed"):
        parse_query("genreClassical")


def test_empty_field_name_raises() -> None:
    with pytest.raises(ValueError, match="malformed"):
        parse_query(":Classical")


def test_empty_query_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_query("")


def test_matches_single_clause_hit() -> None:
    q = parse_query("genre:Classical")
    assert matches(q, {"genre": "Classical"}) is True


def test_matches_single_clause_miss() -> None:
    q = parse_query("genre:Classical")
    assert matches(q, {"genre": "Rock"}) is False


def test_matches_case_insensitive() -> None:
    q = parse_query("genre:classical")
    assert matches(q, {"genre": "CLASSICAL"}) is True


def test_matches_substring() -> None:
    q = parse_query("genre:Class")
    assert matches(q, {"genre": "Classical"}) is True


def test_matches_missing_field_is_miss() -> None:
    q = parse_query("genre:Classical")
    assert matches(q, {}) is False


def test_matches_multi_clause_all_must_hit() -> None:
    q = parse_query("genre:Classical,albumartist:Bach")
    assert matches(q, {"genre": "Classical", "albumartist": "J.S. Bach"}) is True
    assert matches(q, {"genre": "Classical", "albumartist": "Mozart"}) is False
