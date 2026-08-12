"""AST -> compiled closure tree.

Compiles a Template once into a tree of closures ("thunks"), never
re-walking the AST at render time, so rendering tens of thousands of
paths does not reparse the template. Function arguments are compiled to
their own thunks *without being called*, which is what lets %if receive
unevaluated branches and short-circuit properly (evaluate only the
winning branch) — beets evaluates eagerly and papers over this.

Unknown function names are a TemplateError raised here, at compile time,
not the parser — the parser only validates grammar shape.
"""

from __future__ import annotations

from collections.abc import Callable

from muzilla.paths.ast import FuncCall, Literal, Node, Template, Variable
from muzilla.paths.context import RenderContext
from muzilla.paths.errors import TemplateError

CompiledTemplate = Callable[[RenderContext], str]


def compile_template(template: Template, *, source: str = "") -> CompiledTemplate:
    node_thunks = tuple(_compile_node(n, source) for n in template.nodes)

    def render(ctx: RenderContext) -> str:
        return "".join(t(ctx) for t in node_thunks)

    return render


def _compile_node(node: Node, source: str) -> CompiledTemplate:
    if isinstance(node, Literal):
        text = node.text
        return lambda ctx: text
    if isinstance(node, Variable):
        name = node.name
        return lambda ctx: _stringify(ctx.values.get(name))
    if isinstance(node, FuncCall):
        return _compile_func_call(node, source)
    raise AssertionError(f"unhandled node type: {type(node)!r}")  # pragma: no cover


def _compile_func_call(node: FuncCall, source: str) -> CompiledTemplate:
    # Imported here (not at module level) to avoid a circular import:
    # functions.py imports CompiledTemplate/RenderContext from this
    # module's neighbors, and registers into FUNCTIONS which this
    # module reads.
    from muzilla.paths.functions import FUNCTIONS

    func = FUNCTIONS.get(node.name)
    if func is None:
        raise TemplateError(f"unknown function %{node.name}", node.offset, source)

    arg_thunks: tuple[CompiledTemplate, ...] = tuple(
        compile_template(Template(nodes=arg_nodes), source=source) for arg_nodes in node.args
    )

    def call(ctx: RenderContext) -> str:
        return func(ctx, node, *arg_thunks)

    return call


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else ""
    return str(value)
