"""Render-time context for the path template engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

MULTI_VALUE_JOIN = ", "
"""The join delimiter for multi-valued fields (artists, genre, mood)
when stringified into a single $variable — the only existing precedent
in the codebase (frontend TagEditor.tsx / CandidatePicker.tsx both join
with ', ' for display; the Python backend has never joined these to a
string before). Shared between functions.py's %first{} (splits on it)
and render.py's track_to_variables (joins with it), so the two stay in
sync by construction rather than by convention."""


@dataclass(frozen=True, slots=True)
class RenderContext:
    values: Mapping[str, str | int | float | bool | None]
    """$field -> value bindings, already field-name-canonical
    (domain.fields names)."""
