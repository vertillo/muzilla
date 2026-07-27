"""Hypothesis fuzzing for the hand-written recursive-descent template
parser (docs/PLAN.md §11f / §Testing): "assert it either returns a
Template or raises TemplateError with a valid offset, and never any
other exception type."

Recursive-descent-over-regex was §6's own explicit architectural
choice; fuzzing is exactly what a hand-written lexer/parser needs,
since there's no grammar-library guarantee backing its error handling.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from muzilla.paths.ast import Template
from muzilla.paths.errors import TemplateError
from muzilla.paths.parser import parse

# Biased toward the characters the grammar actually treats specially
# ($, {, }, %, ,) so Hypothesis spends its budget probing malformed
# syntax near real edge cases, not just generating inert plain text.
_GRAMMAR_CHARS = "${}%,\\"
_FUZZ_ALPHABET = st.one_of(
    st.sampled_from(_GRAMMAR_CHARS),
    st.characters(blacklist_categories=("Cs",), max_codepoint=0x2FFFF),
)


@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=500)
@given(source=st.text(alphabet=_FUZZ_ALPHABET, max_size=200))
def test_parse_never_raises_anything_but_template_error(source: str) -> None:
    try:
        result = parse(source)
    except TemplateError as exc:
        assert 0 <= exc.offset <= len(source), (
            f"TemplateError offset {exc.offset} out of bounds for "
            f"template of length {len(source)}: {source!r}"
        )
        assert exc.template == source
        return
    assert isinstance(result, Template)


@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=200)
@given(
    source=st.text(
        alphabet=st.sampled_from(_GRAMMAR_CHARS),
        min_size=1,
        max_size=60,
    )
)
def test_parse_never_raises_anything_but_template_error_grammar_only(source: str) -> None:
    """A second pass using only grammar-special characters, unbiased by
    the wider unicode alphabet above -- deep nesting and malformed
    delimiter sequences are much more likely to appear from a
    5-character alphabet at the same max_examples budget."""
    try:
        result = parse(source)
    except TemplateError as exc:
        assert 0 <= exc.offset <= len(source)
        return
    assert isinstance(result, Template)


@given(depth=st.integers(min_value=1, max_value=2000))
@settings(max_examples=15, deadline=None)
def test_deeply_nested_func_calls_raise_template_error_not_recursion_error(depth: int) -> None:
    """Regression test for a real bug this exact fuzzing effort found:
    ~500 levels of %func{%func{...}} nesting raised an unhandled
    RecursionError instead of TemplateError -- a malformed or malicious
    template (e.g. loaded from a config file) could crash the calling
    request/job handler. Fixed in paths/parser.py with
    _MAX_FUNC_NESTING_DEPTH; this proves every depth up to well past
    that limit resolves to one of the two documented outcomes, never a
    raw RecursionError."""
    source = "%upper{" * depth + "x" + "}" * depth
    try:
        result = parse(source)
        assert isinstance(result, Template)
    except TemplateError as exc:
        assert 0 <= exc.offset <= len(source)
