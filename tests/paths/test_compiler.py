from __future__ import annotations

import pytest

from muzilla.paths.compiler import compile_template
from muzilla.paths.context import RenderContext
from muzilla.paths.errors import TemplateError
from muzilla.paths.parser import parse


def _render(template_source: str, values: dict[str, object]) -> str:
    tmpl = parse(template_source)
    compiled = compile_template(tmpl, source=template_source)
    return compiled(RenderContext(values=values))


def test_literal_text() -> None:
    assert _render("hello", {}) == "hello"


def test_variable_substitution() -> None:
    assert _render("$title", {"title": "Abbey Road"}) == "Abbey Road"


def test_missing_variable_renders_empty() -> None:
    assert _render("$missing", {}) == ""


def test_none_value_renders_empty() -> None:
    assert _render("$title", {"title": None}) == ""


def test_int_value_stringified() -> None:
    assert _render("$track", {"track": 7}) == "7"


def test_bool_true_renders_1() -> None:
    assert _render("$comp", {"comp": True}) == "1"


def test_bool_false_renders_empty() -> None:
    assert _render("$comp", {"comp": False}) == ""


def test_text_and_variable_combined() -> None:
    assert _render("$artist - $title", {"artist": "A", "title": "B"}) == "A - B"


def test_function_call_upper() -> None:
    assert _render("%upper{$title}", {"title": "abbey road"}) == "ABBEY ROAD"


def test_nested_function_calls() -> None:
    assert _render("%upper{%lower{$title}}", {"title": "AbBeY"}) == "ABBEY"


def test_unknown_function_raises_at_compile_time() -> None:
    tmpl = parse("%bogus{$title}")
    with pytest.raises(TemplateError) as exc_info:
        compile_template(tmpl, source="%bogus{$title}")
    assert "bogus" in str(exc_info.value)
    assert exc_info.value.offset == 0


def test_unknown_function_error_offset_mid_template() -> None:
    source = "ok $title %bogus{$x}"
    tmpl = parse(source)
    with pytest.raises(TemplateError) as exc_info:
        compile_template(tmpl, source=source)
    assert exc_info.value.offset == source.index("%bogus")


def test_compiled_template_is_reusable_across_renders() -> None:
    tmpl = parse("$title")
    compiled = compile_template(tmpl, source="$title")
    assert compiled(RenderContext(values={"title": "A"})) == "A"
    assert compiled(RenderContext(values={"title": "B"})) == "B"


def test_unevaluated_branch_is_never_invoked() -> None:
    """Proves function args are unevaluated thunks, not eagerly-computed
    values: a spy function is registered as the SECOND argument of a
    dispatcher that only ever calls its first arg. If compile_template
    evaluated arguments eagerly before passing them to the function
    (rather than passing callables the function chooses whether to
    invoke), the spy's side effect would fire regardless of which
    branch "wins" — it doesn't, proving the branch is truly unevaluated."""
    from muzilla.paths.functions import FUNCTIONS

    calls: list[str] = []

    def first_only(ctx, node, a, b):  # type: ignore[no-untyped-def]
        return a(ctx)  # b is deliberately never called

    def spy(ctx, node):  # type: ignore[no-untyped-def]
        calls.append("evaluated")
        return "SPY"

    FUNCTIONS["__test_first_only__"] = first_only
    FUNCTIONS["__test_spy__"] = spy
    try:
        result = _render("%__test_first_only__{$a,%__test_spy__{}}", {"a": "A"})
        assert result == "A"
        assert calls == []  # the spy branch was compiled but never invoked
    finally:
        del FUNCTIONS["__test_first_only__"]
        del FUNCTIONS["__test_spy__"]
