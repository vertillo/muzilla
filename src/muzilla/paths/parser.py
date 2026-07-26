"""Recursive-descent parser for the path template engine (docs/PLAN.md §6):
token stream -> AST.

Function name validity (is %foo a known function?) is deliberately NOT
checked here — that's the compiler's job, so adding a new function to
functions.py never touches this file. The parser only validates grammar
shape: balanced braces, well-formed $var/%func{...} syntax.
"""

from __future__ import annotations

from muzilla.paths.ast import FuncCall, Literal, Node, Template, Variable
from muzilla.paths.errors import TemplateError
from muzilla.paths.lexer import Token, tokenize


def parse(source: str) -> Template:
    """Raises TemplateError with a precise offset on malformed input."""
    tokens = tokenize(source)
    pos = 0

    def peek() -> Token:
        return tokens[pos]

    def advance() -> Token:
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        return tok

    def parse_nodes(stop_kinds: frozenset[str]) -> tuple[Node, ...]:
        nodes: list[Node] = []
        while peek().kind not in stop_kinds:
            tok = peek()
            if tok.kind == "TEXT":
                advance()
                nodes.append(Literal(tok.value, tok.offset))
            elif tok.kind in ("DOLLAR_VAR", "DOLLAR_BRACE_VAR"):
                advance()
                nodes.append(Variable(tok.value, tok.offset))
            elif tok.kind == "PERCENT_FUNC_OPEN":
                nodes.append(parse_func_call())
            elif tok.kind == "EOF":
                # EOF is only a valid stop if the caller allows it;
                # otherwise this is an unterminated construct.
                raise TemplateError(
                    "unexpected end of template — unterminated '{' or unbalanced input",
                    tok.offset,
                    source,
                )
            else:
                raise TemplateError(f"unexpected token {tok.value!r}", tok.offset, source)
        return _merge_adjacent_literals(tuple(nodes))

    def parse_func_call() -> FuncCall:
        open_tok = advance()  # PERCENT_FUNC_OPEN
        args: list[tuple[Node, ...]] = []
        if peek().kind == "BRACE_CLOSE":
            advance()
            return FuncCall(open_tok.value, tuple(args), open_tok.offset)
        while True:
            arg_nodes = parse_nodes(frozenset({"COMMA", "BRACE_CLOSE", "EOF"}))
            args.append(arg_nodes)
            tok = peek()
            if tok.kind == "COMMA":
                advance()
                continue
            if tok.kind == "BRACE_CLOSE":
                advance()
                break
            raise TemplateError(
                f"unterminated function call %{open_tok.value}{{...}}", open_tok.offset, source
            )
        return FuncCall(open_tok.value, tuple(args), open_tok.offset)

    nodes = parse_nodes(frozenset({"EOF"}))
    return Template(nodes)


def _merge_adjacent_literals(nodes: tuple[Node, ...]) -> tuple[Node, ...]:
    """Adjacent Literal nodes get merged into one (minor allocation
    efficiency, keeps the compiled closure tree flatter). Done as a
    post-parse pass rather than inline, to keep parse_nodes simple."""
    if not nodes:
        return nodes
    merged: list[Node] = [nodes[0]]
    for node in nodes[1:]:
        last = merged[-1]
        if isinstance(last, Literal) and isinstance(node, Literal):
            merged[-1] = Literal(last.text + node.text, last.offset)
        else:
            merged.append(node)
    return tuple(merged)
