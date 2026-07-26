from __future__ import annotations

import pytest

from muzilla.paths.ast import FuncCall, Literal, Variable
from muzilla.paths.errors import TemplateError
from muzilla.paths.parser import parse


def test_plain_text() -> None:
    tmpl = parse("hello world")
    assert tmpl.nodes == (Literal("hello world", 0),)


def test_single_variable() -> None:
    tmpl = parse("$title")
    assert tmpl.nodes == (Variable("title", 0),)


def test_brace_variable() -> None:
    tmpl = parse("${title}")
    assert tmpl.nodes == (Variable("title", 0),)


def test_text_and_variable() -> None:
    tmpl = parse("a $title b")
    assert tmpl.nodes == (
        Literal("a ", 0),
        Variable("title", 2),
        Literal(" b", 8),
    )


def test_func_call_no_args() -> None:
    tmpl = parse("%aunique{}")
    assert tmpl.nodes == (FuncCall("aunique", (), 0),)


def test_func_call_with_args() -> None:
    tmpl = parse("%if{$a,$b}")
    assert tmpl.nodes == (
        FuncCall(
            "if",
            (
                (Variable("a", 4),),
                (Variable("b", 7),),
            ),
            0,
        ),
    )


def test_nested_func_call() -> None:
    tmpl = parse("%if{$comp,%upper{$aa},$artist}")
    call = tmpl.nodes[0]
    assert isinstance(call, FuncCall)
    assert call.name == "if"
    assert len(call.args) == 3
    inner = call.args[1][0]
    assert isinstance(inner, FuncCall)
    assert inner.name == "upper"


def test_nesting_depth_three() -> None:
    tmpl = parse("%a{%b{%c{$x}}}")
    outer = tmpl.nodes[0]
    assert isinstance(outer, FuncCall)
    assert outer.name == "a"
    mid = outer.args[0][0]
    assert isinstance(mid, FuncCall)
    assert mid.name == "b"
    inner = mid.args[0][0]
    assert isinstance(inner, FuncCall)
    assert inner.name == "c"
    assert inner.args[0][0] == Variable("x", 9)


def test_adjacent_literals_merged() -> None:
    # The lexer alone wouldn't split "a" and "b" here, but confirm the
    # merge pass at least doesn't break a template with mixed content.
    tmpl = parse("a$$b")
    assert tmpl.nodes == (Literal("a$b", 0),)


def test_bare_trailing_dollar_is_a_literal_not_an_error() -> None:
    # A trailing '$' not followed by an identifier char or '{' is
    # tolerated as a literal '$' (beets' own behavior) -- not malformed.
    tmpl = parse("$")
    assert tmpl.nodes == (Literal("$", 0),)


@pytest.mark.parametrize(
    "template",
    ["${", "${field", "%func{", "%func{a"],
)
def test_malformed_inputs_raise(template: str) -> None:
    with pytest.raises(TemplateError):
        parse(template)


def test_malformed_offset_is_at_problem_start() -> None:
    with pytest.raises(TemplateError) as exc_info:
        parse("ok %func{a")
    # The PERCENT_FUNC_OPEN token starts at offset 3.
    assert exc_info.value.offset == 3


def test_unmatched_trailing_brace_raises() -> None:
    with pytest.raises(TemplateError):
        parse("hello}")
