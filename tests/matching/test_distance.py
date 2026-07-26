from __future__ import annotations

from muzilla.matching.distance import exact_distance, numeric_distance, weighted_distance


def test_weighted_distance_all_identical_is_zero() -> None:
    dist = weighted_distance({"a": 0.0, "b": 0.0}, {"a": 1.0, "b": 2.0})
    assert dist == 0.0


def test_weighted_distance_all_unrelated_is_one() -> None:
    dist = weighted_distance({"a": 1.0, "b": 1.0}, {"a": 1.0, "b": 2.0})
    assert dist == 1.0


def test_weighted_distance_weights_dominant_field_more() -> None:
    heavy_wrong = weighted_distance({"a": 1.0, "b": 0.0}, {"a": 5.0, "b": 1.0})
    light_wrong = weighted_distance({"a": 0.0, "b": 1.0}, {"a": 5.0, "b": 1.0})
    assert heavy_wrong > light_wrong


def test_weighted_distance_ignores_fields_with_no_signal() -> None:
    # "c" isn't in field_distances (no data on either side) -- must not
    # drag the score toward 0 by omission.
    with_signal = weighted_distance({"a": 1.0}, {"a": 1.0, "c": 1.0})
    assert with_signal == 1.0


def test_weighted_distance_empty_weights_is_one() -> None:
    assert weighted_distance({"a": 0.0}, {}) == 1.0


def test_numeric_distance_exact_match() -> None:
    assert numeric_distance(2020, 2020, scale=2) == 0.0


def test_numeric_distance_saturates_at_scale() -> None:
    assert numeric_distance(2020, 2025, scale=2) == 1.0


def test_numeric_distance_missing_value_is_max() -> None:
    assert numeric_distance(None, 2020, scale=2) == 1.0
    assert numeric_distance(2020, None, scale=2) == 1.0


def test_exact_distance() -> None:
    assert exact_distance("abc", "abc") == 0.0
    assert exact_distance("abc", "def") == 1.0
    assert exact_distance(None, "abc") == 1.0
    assert exact_distance(None, None) == 1.0
