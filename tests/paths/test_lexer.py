from __future__ import annotations

import pytest

from muzilla.paths.errors import TemplateError
from muzilla.paths.lexer import Token, tokenize


def _kinds(tokens: list[Token]) -> list[str]:
    return [t.kind for t in tokens]


def test_plain_text() -> None:
    tokens = tokenize("hello world")
    assert _kinds(tokens) == ["TEXT", "EOF"]
    assert tokens[0].value == "hello world"
    assert tokens[0].offset == 0


def test_dollar_var() -> None:
    tokens = tokenize("$albumartist")
    assert _kinds(tokens) == ["DOLLAR_VAR", "EOF"]
    assert tokens[0].value == "albumartist"
    assert tokens[0].offset == 0


def test_dollar_brace_var() -> None:
    tokens = tokenize("${albumartist}")
    assert _kinds(tokens) == ["DOLLAR_BRACE_VAR", "EOF"]
    assert tokens[0].value == "albumartist"


def test_text_around_variable() -> None:
    tokens = tokenize("a $title b")
    assert _kinds(tokens) == ["TEXT", "DOLLAR_VAR", "TEXT", "EOF"]
    assert tokens[0].value == "a "
    assert tokens[1].value == "title"
    assert tokens[2].value == " b"


def test_func_call_no_args() -> None:
    tokens = tokenize("%aunique{}")
    assert _kinds(tokens) == ["PERCENT_FUNC_OPEN", "BRACE_CLOSE", "EOF"]
    assert tokens[0].value == "aunique"


def test_func_call_with_args() -> None:
    tokens = tokenize("%if{$a,$b}")
    assert _kinds(tokens) == [
        "PERCENT_FUNC_OPEN", "DOLLAR_VAR", "COMMA", "DOLLAR_VAR", "BRACE_CLOSE", "EOF",
    ]


def test_nested_func_call() -> None:
    tokens = tokenize("%if{$comp,%upper{$aa},$artist}")
    assert _kinds(tokens) == [
        "PERCENT_FUNC_OPEN", "DOLLAR_VAR", "COMMA",
        "PERCENT_FUNC_OPEN", "DOLLAR_VAR", "BRACE_CLOSE",
        "COMMA", "DOLLAR_VAR", "BRACE_CLOSE", "EOF",
    ]


def test_dollar_dollar_escape() -> None:
    tokens = tokenize("$$5")
    assert _kinds(tokens) == ["TEXT", "EOF"]
    assert tokens[0].value == "$5"


def test_percent_percent_escape() -> None:
    tokens = tokenize("100%%")
    assert _kinds(tokens) == ["TEXT", "EOF"]
    assert tokens[0].value == "100%"


def test_bare_dollar_not_followed_by_ident_is_literal() -> None:
    tokens = tokenize("$5 tracks")
    assert _kinds(tokens) == ["TEXT", "EOF"]
    assert tokens[0].value == "$5 tracks"


def test_unterminated_dollar_brace_raises_with_offset() -> None:
    with pytest.raises(TemplateError) as exc_info:
        tokenize("${field")
    assert exc_info.value.offset == 0


def test_unterminated_func_call_raises_with_offset() -> None:
    with pytest.raises(TemplateError) as exc_info:
        tokenize("%func")
    assert exc_info.value.offset == 0


def test_offsets_are_precise() -> None:
    tokens = tokenize("ab$title")
    assert tokens[0].offset == 0  # "ab"
    assert tokens[1].offset == 2  # $title
