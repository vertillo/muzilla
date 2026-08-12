"""Top-level render entrypoint for the path template engine (docs/product-spec.md).

Ties the lexer/parser/compiler/functions/sanitize/query modules together
into the single public API the service layer calls: turn a plain field
values dict into $variable bindings, compile+render a template against
them, split on '/' and sanitize each component independently, and surface
the `create_directories: false` + rendered-'/' case as a structured
validation result rather than an exception (a live-preview caller needs
a response, not a crash).
"""

from __future__ import annotations

from dataclasses import dataclass

from muzilla.domain import fields as field_registry
from muzilla.domain.fields import FieldType
from muzilla.paths.compiler import CompiledTemplate, compile_template
from muzilla.paths.context import MULTI_VALUE_JOIN, RenderContext
from muzilla.paths.errors import RenderError, TemplateError
from muzilla.paths.parser import parse
from muzilla.paths.sanitize import sanitize_component

Variables = dict[str, str | int | float | bool | None]

_BEETS_ALIASES: dict[str, str] = {
    # docs/product-spec.md own example templates ("$albumartist - $album -
    # $track $title") use beets' conventional short names, which don't
    # match domain/fields.py's canonical snake_case names
    # (album_artist, track_no, ...) — this project's single source of
    # truth for field data. Rather than force every template author to
    # write $album_artist, both spellings resolve to the same value:
    # the canonical name always works, and these aliases exist so the
    # plan's own example templates work exactly as written.
    "albumartist": "album_artist",
    "track": "track_no",
    "tracktotal": "track_total",
    "disc": "disc_no",
    "disctotal": "disc_total",
    "albumtype": "compilation",
    "catalognum": "catalog_number",
}


def track_to_variables(values: dict[str, object]) -> Variables:
    """Maps a plain field-name -> value dict (already produced by the
    caller — this module does NOT read Track/TrackGroup ORM rows
    directly, staying DB-free) into the $variable space a compiled
    template evaluates against. Scalar fields pass through 1:1;
    MULTI_TEXT fields (artists, genre, mood) get joined with the same
    ', ' delimiter %first{} splits on, so $genre in a template renders
    sensibly without an explicit %first{} call. Also populates beets-
    style short aliases (albumartist, track, ...) pointing at the same
    values as their canonical domain.fields names, so templates can use
    either spelling."""
    result: Variables = {}
    for name, value in values.items():
        fdef = field_registry.FIELDS.get(name)
        if fdef is not None and fdef.type == FieldType.MULTI_TEXT:
            if value is None:
                result[name] = None
            elif isinstance(value, list | tuple):
                result[name] = MULTI_VALUE_JOIN.join(str(v) for v in value)
            else:
                result[name] = str(value)
            continue
        if isinstance(value, str | int | float | bool) or value is None:
            result[name] = value
        else:
            result[name] = str(value)

    for alias, canonical in _BEETS_ALIASES.items():
        if canonical in result:
            result[alias] = result[canonical]

    return result


def compile(template_source: str) -> CompiledTemplate:
    """Thin re-export of paths.compiler, exposed here so callers doing
    the "compile once, render N times" batch case (the plan's explicit
    "compiles once so rendering 50k paths doesn't reparse" requirement)
    only need to import paths.render, not reach into paths.compiler and
    paths.parser separately."""
    tmpl = parse(template_source)
    return compile_template(tmpl, source=template_source)


@dataclass(frozen=True, slots=True)
class RenderResult:
    path: str
    """Rendered, sanitized, relative path (components joined by '/')."""
    components: tuple[str, ...]
    """path split on '/', each component already sanitized independently."""
    errors: tuple[str, ...] = ()
    """Non-fatal render-time warnings (e.g. unresolved %aunique, or a
    validation failure like a '/' under create_directories=False) —
    stringified so a live-preview caller gets a plain structured
    response rather than needing to catch exceptions."""


def render(
    compiled: CompiledTemplate,
    ctx: RenderContext,
    *,
    create_directories: bool,
    replacements: tuple[tuple[str, str], ...] = (),
) -> RenderResult:
    """Renders a pre-compiled template against ctx. See compile_and_render
    for the common single-call case."""
    errors: list[str] = []
    try:
        rendered = compiled(ctx)
    except RenderError as exc:
        return RenderResult(path="", components=(), errors=(str(exc),))

    if not create_directories and "/" in rendered:
        errors.append(
            f"template rendered a '/' but create_directories is disabled: {rendered!r}"
        )
        return RenderResult(path=rendered, components=(rendered,), errors=tuple(errors))

    raw_components = rendered.split("/")
    if any(c.strip() == "" for c in raw_components):
        errors.append(f"template rendered an empty path component: {rendered!r}")
        return RenderResult(path=rendered, components=tuple(raw_components), errors=tuple(errors))

    sanitized = tuple(
        sanitize_component(c, replacements=replacements) for c in raw_components
    )
    return RenderResult(path="/".join(sanitized), components=sanitized, errors=tuple(errors))


def compile_and_render(
    template_source: str,
    ctx: RenderContext,
    *,
    create_directories: bool,
    replacements: tuple[tuple[str, str], ...] = (),
) -> RenderResult:
    """Parses+compiles template_source and renders against ctx in one
    call — the common case (CLI path-test, single-track live preview).
    Raises TemplateError for a malformed/unknown-function template
    (compile-time, offset-precise); render-time failures are returned
    structurally via RenderResult.errors, never raised, since a
    live-preview caller needs a response even when this one track's
    data doesn't render cleanly."""
    try:
        compiled = compile(template_source)
    except TemplateError:
        raise
    return render(
        compiled, ctx, create_directories=create_directories, replacements=replacements
    )


__all__ = [
    "RenderResult",
    "Variables",
    "compile",
    "compile_and_render",
    "render",
    "track_to_variables",
]
