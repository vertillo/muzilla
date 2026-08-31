"""AST node types for the path template engine.

Plain frozen dataclasses, no behavior — mirrors changes/differ.py's
FieldDiff/InlineSpan style (data-only; parsing lives in parser.py,
rendering logic lives in compiler.py/functions.py). Every node carries the
lexer offset it was parsed from, so compile-time errors (unknown function
names — checked by the compiler, not the parser) can still cite a precise
source location.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Literal:
    text: str
    offset: int


@dataclass(frozen=True, slots=True)
class Variable:
    name: str
    """Canonical field name, e.g. "albumartist" (from $albumartist or
    ${albumartist})."""
    offset: int


@dataclass(frozen=True, slots=True)
class FuncCall:
    name: str
    """Function name without the leading %, e.g. "if", "pad", "upper"."""
    args: tuple[tuple[Node, ...], ...]
    """Each argument is itself a sequence of nodes (a sub-template) —
    %func{a,b} has two args, each independently a Template-shaped node list."""
    offset: int


Node = Literal | Variable | FuncCall


@dataclass(frozen=True, slots=True)
class Template:
    nodes: tuple[Node, ...]
