"""Chunking helper for SQLite ``IN (...)`` and ``NOT IN (...)`` queries.

SQLite builds impose a bind-variable limit. Keeping batches at 500 avoids
exceeding common limits while retaining deterministic iteration order.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Sequence

_DEFAULT_BATCH_SIZE = 500


def batched(ids: Collection[int], *, size: int = _DEFAULT_BATCH_SIZE) -> Iterator[Sequence[int]]:
    """Yields `ids` in fixed-size chunks, order preserved from iteration
    order of `ids` (pass a list, not a set, if reproducible batch
    composition matters — e.g. for deterministic test assertions)."""
    ids_list = list(ids)
    for i in range(0, len(ids_list), size):
        yield ids_list[i : i + size]
