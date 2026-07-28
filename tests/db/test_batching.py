from __future__ import annotations

from muzilla.db.batching import batched


def test_batched_chunks_at_default_size() -> None:
    ids = list(range(1250))
    chunks = list(batched(ids))
    assert [len(c) for c in chunks] == [500, 500, 250]
    assert [id_ for chunk in chunks for id_ in chunk] == ids


def test_batched_respects_custom_size() -> None:
    ids = list(range(10))
    chunks = list(batched(ids, size=3))
    assert [list(c) for c in chunks] == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]


def test_batched_empty_input_yields_nothing() -> None:
    assert list(batched([])) == []


def test_batched_preserves_order_for_a_list_not_a_set() -> None:
    # Passing a set would make batch composition nondeterministic across
    # runs — this pins that a list's iteration order survives untouched.
    ids = [5, 3, 1, 4, 2]
    assert list(batched(ids, size=2)) == [[5, 3], [1, 4], [2]]
