"""Render-time context for the path template engine (docs/PLAN.md §6).

`DisambiguationResolver` is a Protocol, not a concrete class, so this
module never imports the database — the layering table in docs/PLAN.md
lists `paths -> domain` only (`changes` is the one allowed `db, tags,
paths`), and the whole engine stays unit-testable with zero DB fixtures.
The concrete DB-backed implementation lives in services/paths.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


class DisambiguationResolver(Protocol):
    def resolve(self, key: str) -> str | None:
        """`key` identifies the album/singleton being rendered (its
        disambiguation grouping fields, joined — see paths/functions.py's
        %aunique/%sunique). The resolver owns looking up every OTHER
        item sharing this key from the batch it was constructed over
        (services/paths.py's DbDisambiguationResolver does this against
        the *projected post-change* values, not current DB state — the
        "ordering trap" docs/PLAN.md §6 calls out) and returns the first
        field name (a fixed precedence — year, label, catalog_number,
        mbid_prefix) whose value differs from at least one sibling, or
        None if nothing collides or nothing separates the collision."""
        ...


@dataclass(frozen=True, slots=True)
class RenderContext:
    values: Mapping[str, str | int | float | bool | None]
    """$field -> value bindings, already field-name-canonical
    (domain.fields names)."""
    resolver: DisambiguationResolver | None = None
    """Required only if the template calls %aunique/%sunique; None is
    fine for templates that don't."""
    batch_key: str | None = None
    """Identifies which batch this render belongs to, so %aunique's
    memoization (docs/PLAN.md: "memoized per album per batch") can be
    scoped correctly by a resolver implementation that caches internally."""
    warnings: list[str] = field(default_factory=list)
    """Render-time warnings collected during evaluation (e.g. an
    unresolved %aunique collision) — appended to by functions, read by
    the caller after rendering completes. A list, not a return value,
    since a CompiledTemplate's signature is fixed at `(ctx) -> str`."""
