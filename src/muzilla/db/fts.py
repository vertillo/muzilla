"""Shared helpers for safe, literal SQLite FTS5 searches."""

from __future__ import annotations


def encode_fts5_literal(value: str | None) -> str | None:
    """Return one quoted FTS5 phrase, or ``None`` for an empty filter.

    FTS5 otherwise interprets punctuation and words such as ``OR`` as
    query syntax. Doubling quotes is FTS5's escape form inside a quoted
    phrase, so every caller gets literal text semantics without having
    to know the query language.
    """
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    escaped = normalized.replace('"', '""')
    return f'"{escaped}"'
