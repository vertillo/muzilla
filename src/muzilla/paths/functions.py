"""Function library for the path template engine (docs/PLAN.md §6):
%upper %lower %title %left %right %if %ifdef %asciify %time %first
%aunique %sunique %the, plus muzilla additions %pad %sanitize %default.

Registered by name into FUNCTIONS, looked up by compiler.py at compile
time. A function receives the RenderContext, the raw FuncCall AST node
(so a function like %ifdef can inspect a literal argument — a bare field
name — without it being evaluated as a thunk), and one compiled thunk
per argument, *unevaluated*. The function decides which thunks to
actually call — this is what makes %if short-circuit correctly.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from datetime import datetime

from muzilla.paths.ast import FuncCall, Literal
from muzilla.paths.compiler import CompiledTemplate
from muzilla.paths.context import MULTI_VALUE_JOIN, RenderContext
from muzilla.paths.errors import RenderError, TemplateError

FuncImpl = Callable[..., str]
"""(ctx: RenderContext, node: FuncCall, *arg_thunks: CompiledTemplate) -> str"""

FUNCTIONS: dict[str, FuncImpl] = {}


def register(name: str) -> Callable[[FuncImpl], FuncImpl]:
    def decorator(fn: FuncImpl) -> FuncImpl:
        FUNCTIONS[name] = fn
        return fn

    return decorator


def _require_args(node: FuncCall, count_min: int, count_max: int | None = None) -> None:
    n = len(node.args)
    upper = count_max if count_max is not None else count_min
    if not (count_min <= n <= upper):
        expected = f"{count_min}" if count_min == upper else f"{count_min}-{upper}"
        raise TemplateError(
            f"%{node.name} expects {expected} argument(s), got {n}", node.offset, ""
        )


# --- Pure string functions --------------------------------------------


@register("upper")
def _upper(ctx: RenderContext, node: FuncCall, a: CompiledTemplate) -> str:
    return a(ctx).upper()


@register("lower")
def _lower(ctx: RenderContext, node: FuncCall, a: CompiledTemplate) -> str:
    return a(ctx).lower()


@register("title")
def _title(ctx: RenderContext, node: FuncCall, a: CompiledTemplate) -> str:
    # str.title() mistitles apostrophes ("don't" -> "Don'T") — capitalize
    # only after whitespace/hyphen boundaries instead.
    text = a(ctx)
    result = []
    capitalize_next = True
    for ch in text:
        if capitalize_next and ch.isalpha():
            result.append(ch.upper())
            capitalize_next = False
        else:
            result.append(ch.lower())
        if ch in " -":
            capitalize_next = True
    return "".join(result)


@register("left")
def _left(ctx: RenderContext, node: FuncCall, a: CompiledTemplate, n: CompiledTemplate) -> str:
    text = a(ctx)
    try:
        count = int(n(ctx))
    except ValueError as exc:
        raise RenderError(f"%left: {n(ctx)!r} is not an integer") from exc
    return text[:count]


@register("right")
def _right(ctx: RenderContext, node: FuncCall, a: CompiledTemplate, n: CompiledTemplate) -> str:
    text = a(ctx)
    try:
        count = int(n(ctx))
    except ValueError as exc:
        raise RenderError(f"%right: {n(ctx)!r} is not an integer") from exc
    return text[-count:] if count > 0 else ""


@register("if")
def _if(
    ctx: RenderContext,
    node: FuncCall,
    cond: CompiledTemplate,
    then: CompiledTemplate,
    else_: CompiledTemplate | None = None,
) -> str:
    if cond(ctx).strip():
        return then(ctx)
    return else_(ctx) if else_ is not None else ""


@register("ifdef")
def _ifdef(
    ctx: RenderContext,
    node: FuncCall,
    field_thunk: CompiledTemplate,
    then: CompiledTemplate | None = None,
    else_: CompiledTemplate | None = None,
) -> str:
    # Field-name presence test, not thunk-value test: $field evaluated as
    # a thunk already stringifies a missing value to "", indistinguishable
    # from a present-but-empty field. So this reaches into the raw AST to
    # require the first argument be exactly one bare literal field name
    # (beets syntax: %ifdef{field,...}, no leading $ — a literal, not a
    # $variable reference).
    first_arg_nodes = node.args[0] if node.args else ()
    if len(first_arg_nodes) != 1 or not isinstance(first_arg_nodes[0], Literal):
        raise TemplateError(
            "%ifdef's first argument must be a bare field name, e.g. %ifdef{title,...}",
            node.offset,
            "",
        )
    field_name = first_arg_nodes[0].text
    is_defined = ctx.values.get(field_name) is not None
    if is_defined:
        return then(ctx) if then is not None else _stringify_field(ctx, field_name)
    return else_(ctx) if else_ is not None else ""


def _stringify_field(ctx: RenderContext, field_name: str) -> str:
    value = ctx.values.get(field_name)
    return "" if value is None else str(value)


_ASCIIFY_SUPPLEMENT = {
    "ß": "ss", "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O",
    "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "ł": "l", "Ł": "L",
}


@register("asciify")
def _asciify(ctx: RenderContext, node: FuncCall, a: CompiledTemplate) -> str:
    text = a(ctx)
    text = "".join(_ASCIIFY_SUPPLEMENT.get(ch, ch) for ch in text)
    # Same NFKD-decompose + strip-combining-marks technique as
    # domain/normalize.py's normalize_for_match — but NOT that function
    # itself, which also casefolds and strips articles/punctuation for
    # fuzzy matching, wrong for a path-template function that must
    # preserve case and word structure.
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_DATE_INPUT_FORMATS = ("%Y-%m-%d", "%Y-%m", "%Y")


@register("time")
def _time(ctx: RenderContext, node: FuncCall, date_thunk: CompiledTemplate, fmt_thunk: CompiledTemplate) -> str:
    """fmt is a Python strftime format string, e.g. %time{$date,%%Y-%%m}.
    The %% (not single %) is required: a bare %Y in a template argument
    is indistinguishable from an attempted %Y{...} function call to the
    lexer, which has no per-function argument semantics — %% is the
    escape the engine already defines for a literal '%' everywhere else,
    so this reuses it rather than special-casing %time's grammar."""
    date_str = date_thunk(ctx).strip()
    fmt = fmt_thunk(ctx)
    if not date_str:
        return ""
    for input_fmt in _DATE_INPUT_FORMATS:
        try:
            parsed = datetime.strptime(date_str, input_fmt)
        except ValueError:
            continue
        return parsed.strftime(fmt)
    raise RenderError(f"%time: could not parse date {date_str!r}")


@register("first")
def _first(ctx: RenderContext, node: FuncCall, a: CompiledTemplate) -> str:
    text = a(ctx)
    if not text:
        return ""
    return text.split(MULTI_VALUE_JOIN)[0]


@register("the")
def _the(ctx: RenderContext, node: FuncCall, a: CompiledTemplate, mode: CompiledTemplate | None = None) -> str:
    text = a(ctx)
    mode_value = mode(ctx).strip().lower() if mode is not None else "move"
    for article in ("The ", "the ", "A ", "a ", "An ", "an "):
        if text.startswith(article):
            rest = text[len(article) :]
            stripped_article = article.strip()
            if mode_value == "strip":
                return rest
            return f"{rest}, {stripped_article}"
    return text


@register("pad")
def _pad(ctx: RenderContext, node: FuncCall, a: CompiledTemplate, width_thunk: CompiledTemplate) -> str:
    text = a(ctx)
    try:
        width = int(width_thunk(ctx))
    except ValueError as exc:
        raise RenderError(f"%pad: width {width_thunk(ctx)!r} is not an integer") from exc
    return text.zfill(width)


@register("default")
def _default(ctx: RenderContext, node: FuncCall, a: CompiledTemplate, fallback: CompiledTemplate) -> str:
    value = a(ctx)
    return value if value else fallback(ctx)


@register("sanitize")
def _sanitize(ctx: RenderContext, node: FuncCall, a: CompiledTemplate) -> str:
    from muzilla.paths.sanitize import sanitize_component

    return sanitize_component(a(ctx))


# --- Batch/DB-context functions -----------------------------------------

_AUNIQUE_DEFAULT_KEYS = ("albumartist", "album")
_SUNIQUE_DEFAULT_KEYS = ("artist", "title")
_DISAMBIGUATOR_ORDER = ("year", "label", "catalog_number", "mbid_prefix")


def _unique_impl(
    ctx: RenderContext,
    node: FuncCall,
    default_keys: tuple[str, ...],
) -> str:
    if ctx.resolver is None:
        raise RenderError(
            f"%{node.name} called with no DisambiguationResolver in context "
            "(this template needs batch context to disambiguate)"
        )
    key_values = tuple(str(ctx.values.get(k, "") or "") for k in default_keys)
    key = "\x1f".join(key_values)
    # The resolver (services/paths.py's DbDisambiguationResolver) owns
    # looking up every sibling sharing `key` from the projected
    # post-change batch and returns which FIELD separates them (e.g.
    # "year") — not that field's value. The value to render is this
    # item's own value for that field, read from ctx.values like any
    # other variable reference.
    separating_field = ctx.resolver.resolve(key)
    if separating_field is None:
        return ""
    value = ctx.values.get(separating_field)
    if value is None:
        return ""
    return f" [{value}]"


@register("aunique")
def _aunique(ctx: RenderContext, node: FuncCall) -> str:
    return _unique_impl(ctx, node, _AUNIQUE_DEFAULT_KEYS)


@register("sunique")
def _sunique(ctx: RenderContext, node: FuncCall) -> str:
    return _unique_impl(ctx, node, _SUNIQUE_DEFAULT_KEYS)
