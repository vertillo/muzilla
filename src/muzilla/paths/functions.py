"""Function library for the path template engine (docs/PLAN.md §6).

Registered by name into FUNCTIONS, looked up by compiler.py at compile
time. A function receives the RenderContext, the raw FuncCall AST node
(so a function like %ifdef can inspect a literal argument — e.g. a bare
field name — without it being evaluated as a thunk), and one compiled
thunk per argument, *unevaluated*. The function decides which thunks to
actually call — this is what makes %if short-circuit correctly.

This module is intentionally minimal for now (compiler-development
step) — the full function list lands in a follow-up commit.
"""

from __future__ import annotations

from collections.abc import Callable

from muzilla.paths.ast import FuncCall
from muzilla.paths.compiler import CompiledTemplate
from muzilla.paths.context import RenderContext

FuncImpl = Callable[..., str]
"""(ctx: RenderContext, node: FuncCall, *arg_thunks: CompiledTemplate) -> str"""

FUNCTIONS: dict[str, FuncImpl] = {}


def register(name: str) -> Callable[[FuncImpl], FuncImpl]:
    def decorator(fn: FuncImpl) -> FuncImpl:
        FUNCTIONS[name] = fn
        return fn

    return decorator


@register("upper")
def _upper(ctx: RenderContext, node: FuncCall, a: CompiledTemplate) -> str:
    return a(ctx).upper()


@register("lower")
def _lower(ctx: RenderContext, node: FuncCall, a: CompiledTemplate) -> str:
    return a(ctx).lower()
