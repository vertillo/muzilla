"""Query-keyed template override matching (docs/PLAN.md §6):

    paths:
      "genre:Classical": "Classical/$composer/$album/$track $title"

A tiny hand-rolled field:value matcher, ANDed across comma-separated
clauses — not a query grammar (there's no boolean OR/negation, no
existing query mini-language in this codebase to extend). Operates on
the already-rendered variable-context dict, never the DB.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldQuery:
    clauses: tuple[tuple[str, str], ...]
    """(field, value) pairs, ANDed."""


def parse_query(raw: str) -> FieldQuery:
    """'genre:Classical' or 'genre:Classical,albumartist:Bach' (comma-
    separated, ANDed) -> FieldQuery. Raises ValueError on a clause
    missing ':'."""
    clauses: list[tuple[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"malformed query clause {part!r} — expected 'field:value'")
        field, _, value = part.partition(":")
        field = field.strip()
        value = value.strip()
        if not field:
            raise ValueError(f"malformed query clause {part!r} — empty field name")
        clauses.append((field, value))
    if not clauses:
        raise ValueError(f"empty query: {raw!r}")
    return FieldQuery(tuple(clauses))


def matches(query: FieldQuery, values: Mapping[str, object]) -> bool:
    """Case-insensitive substring match per clause against
    str(values.get(field)) — every clause must match (AND)."""
    for field, value in query.clauses:
        actual = values.get(field)
        actual_str = "" if actual is None else str(actual)
        if value.lower() not in actual_str.lower():
            return False
    return True
