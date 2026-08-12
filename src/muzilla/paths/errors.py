"""Error types for the path template engine.

Two distinct kinds, since they need different UI treatment: `TemplateError`
is a compile-time syntax/name error with a precise source offset (the
template editor renders a caret under it); `RenderError` is a render-time
failure for one specific track's data (a syntactically valid template that,
e.g., calls %aunique with no batch context) — the live-preview table shows
these as a warning row, not a red squiggly.
"""

from __future__ import annotations


class TemplateError(Exception):
    """Malformed template syntax or an unknown function name, raised while
    parsing/compiling. `offset` is the character offset into `template`
    where the problem starts, so an editor can point a caret at it."""

    def __init__(self, message: str, offset: int, template: str) -> None:
        super().__init__(message)
        self.message = message
        self.offset = offset
        self.template = template


class RenderError(Exception):
    """A syntactically valid template failed to render for one specific
    track's data (e.g. %aunique called with no DisambiguationResolver, or
    %time given an unparseable date). Distinct from TemplateError: this is
    about the data, not the template's grammar."""
