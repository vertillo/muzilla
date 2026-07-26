"""Hand-written tokenizer for the path template engine (docs/PLAN.md §6).

No regex — a plain char-by-char scanner tracking the character offset of
every emitted token, since TemplateError needs precise offsets for the
future template editor's caret. Not a parser generator either (~250 lines
total across lexer+parser+compiler doesn't justify the dependency, and
nesting like %if{$comp,%upper{$aa},$artist} needs recursive-descent anyway).

Syntax (beets-compatible): $field, ${field}, %func{a,b,...} with nesting,
$$ and %% as literal escapes.
"""

from __future__ import annotations

from dataclasses import dataclass

from muzilla.paths.errors import TemplateError

_IDENT_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENT_CONT = _IDENT_START | set("0123456789")


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    """TEXT | DOLLAR_VAR | DOLLAR_BRACE_VAR | PERCENT_FUNC_OPEN | COMMA |
    BRACE_CLOSE | EOF"""
    value: str
    offset: int


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(source)
    text_start: int | None = None
    text_buf: list[str] = []

    def flush_text(end: int) -> None:
        nonlocal text_start, text_buf
        if text_buf:
            tokens.append(Token("TEXT", "".join(text_buf), text_start if text_start is not None else end))
            text_buf.clear()
        text_start = None

    while i < n:
        ch = source[i]

        if ch == "$":
            if i + 1 < n and source[i + 1] == "$":
                if text_start is None:
                    text_start = i
                text_buf.append("$")
                i += 2
                continue
            if i + 1 < n and source[i + 1] == "{":
                flush_text(i)
                close = source.find("}", i + 2)
                if close == -1:
                    raise TemplateError("unterminated '${' — missing closing '}'", i, source)
                name = source[i + 2 : close]
                if not name or not _is_ident(name):
                    raise TemplateError(f"invalid field name {name!r} in '${{...}}'", i, source)
                tokens.append(Token("DOLLAR_BRACE_VAR", name, i))
                i = close + 1
                continue
            if i + 1 < n and source[i + 1] in _IDENT_START:
                flush_text(i)
                j = i + 1
                while j < n and source[j] in _IDENT_CONT:
                    j += 1
                tokens.append(Token("DOLLAR_VAR", source[i + 1 : j], i))
                i = j
                continue
            # Bare '$' not followed by an identifier char or '{' — beets
            # tolerates this as a literal '$'.
            if text_start is None:
                text_start = i
            text_buf.append("$")
            i += 1
            continue

        if ch == "%":
            if i + 1 < n and source[i + 1] == "%":
                if text_start is None:
                    text_start = i
                text_buf.append("%")
                i += 2
                continue
            if i + 1 < n and source[i + 1] in _IDENT_START:
                flush_text(i)
                j = i + 1
                while j < n and source[j] in _IDENT_CONT:
                    j += 1
                if j >= n or source[j] != "{":
                    raise TemplateError(
                        f"expected '{{' after function name %{source[i + 1 : j]}", i, source
                    )
                tokens.append(Token("PERCENT_FUNC_OPEN", source[i + 1 : j], i))
                i = j + 1
                continue
            # Bare '%' not starting a function — literal.
            if text_start is None:
                text_start = i
            text_buf.append("%")
            i += 1
            continue

        if ch == ",":
            flush_text(i)
            tokens.append(Token("COMMA", ",", i))
            i += 1
            continue

        if ch == "}":
            flush_text(i)
            tokens.append(Token("BRACE_CLOSE", "}", i))
            i += 1
            continue

        if text_start is None:
            text_start = i
        text_buf.append(ch)
        i += 1

    flush_text(n)
    tokens.append(Token("EOF", "", n))
    return tokens


def _is_ident(name: str) -> bool:
    if not name or name[0] not in _IDENT_START:
        return False
    return all(c in _IDENT_CONT for c in name[1:])
