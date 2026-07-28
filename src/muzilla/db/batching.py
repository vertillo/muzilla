"""Chunking helper for SQLite `IN (...)` / `NOT IN (...)` queries.

SQLite's SQLITE_MAX_VARIABLE_NUMBER is 999 on older builds, 32766 on
SQLite >=3.32 — passing every id in a large collection as bind
parameters to one `Column.in_(ids)` call raises
`sqlite3.OperationalError: too many SQL variables` once a library-wide
operation's id set exceeds whichever limit the runtime SQLite build
has. docs/PLAN.md §11g's 100k-track performance pass hit this in
`pipeline/grouping.py`, and a follow-up review (§11m) found the same
shape unbatched in three more call sites in `services/paths.py` —
including one in the very function §11g's fix landed in. Centralizing
the chunk-size decision here means a batched call site only has to get
the loop right once, and any future large-`IN()` query reaches for this
instead of a fourth hand-rolled copy.

500 is comfortably under either limit and is not meant to be tuned —
don't try to detect the runtime ceiling; it depends on the SQLite
build's compile flags, not just its version.
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
