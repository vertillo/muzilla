from __future__ import annotations

import pytest

from muzilla.paths.compiler import compile_template
from muzilla.paths.context import RenderContext
from muzilla.paths.errors import RenderError, TemplateError
from muzilla.paths.parser import parse


def _render(template_source: str, values: dict[str, object] | None = None, **kwargs: object) -> str:
    tmpl = parse(template_source)
    compiled = compile_template(tmpl, source=template_source)
    return compiled(RenderContext(values=values or {}, **kwargs))  # type: ignore[arg-type]


# --- %upper / %lower / %title ---------------------------------------------


def test_upper() -> None:
    assert _render("%upper{$t}", {"t": "abc"}) == "ABC"


def test_lower() -> None:
    assert _render("%lower{$t}", {"t": "ABC"}) == "abc"


def test_title() -> None:
    assert _render("%title{$t}", {"t": "abbey road"}) == "Abbey Road"


def test_title_preserves_apostrophe_casing() -> None:
    assert _render("%title{$t}", {"t": "don't stop"}) == "Don't Stop"


def test_title_empty_string() -> None:
    assert _render("%title{$t}", {"t": ""}) == ""


# --- %left / %right ---------------------------------------------------------


def test_left() -> None:
    assert _render("%left{$t,3}", {"t": "abcdef"}) == "abc"


def test_right() -> None:
    assert _render("%right{$t,3}", {"t": "abcdef"}) == "def"


def test_left_beyond_length() -> None:
    assert _render("%left{$t,10}", {"t": "abc"}) == "abc"


def test_left_zero() -> None:
    assert _render("%left{$t,0}", {"t": "abc"}) == ""


# --- %if / %ifdef -----------------------------------------------------------


def test_if_true_branch() -> None:
    assert _render("%if{$comp,YES,NO}", {"comp": "1"}) == "YES"


def test_if_false_branch() -> None:
    assert _render("%if{$comp,YES,NO}", {"comp": ""}) == "NO"


def test_if_missing_condition_is_false() -> None:
    assert _render("%if{$missing,YES,NO}", {}) == "NO"


def test_if_two_arg_form_no_else() -> None:
    assert _render("%if{$comp,YES}", {"comp": ""}) == ""
    assert _render("%if{$comp,YES}", {"comp": "1"}) == "YES"


def test_ifdef_present_field() -> None:
    assert _render("%ifdef{title,HAS,NO}", {"title": "x"}) == "HAS"


def test_ifdef_missing_field() -> None:
    assert _render("%ifdef{title,HAS,NO}", {}) == "NO"


def test_ifdef_present_but_empty_string_is_still_defined() -> None:
    # Distinguishes %ifdef (field presence) from %if (value truthiness):
    # an empty-but-present field is "defined".
    assert _render("%ifdef{title,HAS,NO}", {"title": ""}) == "HAS"


def test_ifdef_default_form_echoes_value() -> None:
    assert _render("%ifdef{title}", {"title": "x"}) == "x"
    assert _render("%ifdef{title}", {}) == ""


def test_ifdef_rejects_dollar_variable_first_arg() -> None:
    # %ifdef's first arg must be a bare field name (beets syntax:
    # %ifdef{title,...}), not a $variable reference — $title would
    # already be a thunk that stringifies a missing field to "",
    # making presence indistinguishable from empty. This check happens
    # at render time (inside the function body), not compile time,
    # since the compiler never calls into a function's implementation.
    tmpl = parse("%ifdef{$title,HAS,NO}")
    compiled = compile_template(tmpl, source="%ifdef{$title,HAS,NO}")
    with pytest.raises(TemplateError):
        compiled(RenderContext(values={"title": "x"}))


# --- %asciify ----------------------------------------------------------------


def test_asciify_accented_latin() -> None:
    assert _render("%asciify{$t}", {"t": "Sigur Rós"}) == "Sigur Ros"


def test_asciify_supplement_table() -> None:
    assert _render("%asciify{$t}", {"t": "Mötley Crüe"}) == "Motley Crue"
    assert _render("%asciify{$t}", {"t": "Björk"}) == "Bjork"
    assert _render("%asciify{$t}", {"t": "Weiß"}) == "Weiss"


def test_asciify_preserves_case() -> None:
    assert _render("%asciify{$t}", {"t": "ÀÉÎ"}) == "AEI"


# --- %time -------------------------------------------------------------------


def test_time_full_date() -> None:
    # %% escapes a literal '%' -- a bare %Y in a template argument would
    # otherwise be lexed as an attempted %Y{...} function call.
    assert _render("%time{$d,%%Y}", {"d": "1999-03-14"}) == "1999"


def test_time_year_only_input() -> None:
    assert _render("%time{$d,%%Y}", {"d": "1999"}) == "1999"


def test_time_reformats() -> None:
    assert _render("%time{$d,%%Y-%%m}", {"d": "1999-03-14"}) == "1999-03"


def test_time_empty_input_is_empty_output() -> None:
    assert _render("%time{$d,%%Y}", {"d": ""}) == ""


def test_time_unparseable_raises_render_error() -> None:
    tmpl = parse("%time{$d,%%Y}")
    compiled = compile_template(tmpl, source="%time{$d,%%Y}")
    with pytest.raises(RenderError):
        compiled(RenderContext(values={"d": "not-a-date"}))


# --- %first ------------------------------------------------------------------


def test_first_multi_value() -> None:
    assert _render("%first{$genre}", {"genre": "Rock, Pop, Jazz"}) == "Rock"


def test_first_single_value() -> None:
    assert _render("%first{$genre}", {"genre": "Rock"}) == "Rock"


def test_first_empty() -> None:
    assert _render("%first{$genre}", {"genre": ""}) == ""


def test_first_missing() -> None:
    assert _render("%first{$genre}", {}) == ""


# --- %the --------------------------------------------------------------------


def test_the_moves_article_to_end_by_default() -> None:
    assert _render("%the{$t}", {"t": "The Beatles"}) == "Beatles, The"


def test_the_strip_mode() -> None:
    assert _render("%the{$t,strip}", {"t": "The Beatles"}) == "Beatles"


def test_the_no_article_unchanged() -> None:
    assert _render("%the{$t}", {"t": "Beatles"}) == "Beatles"


def test_the_indefinite_article() -> None:
    assert _render("%the{$t}", {"t": "A Tribe Called Quest"}) == "Tribe Called Quest, A"


# --- %pad (muzilla addition) --------------------------------------------------


def test_pad_zero_fills() -> None:
    assert _render("%pad{$track,2}", {"track": "7"}) == "07"


def test_pad_already_wide_enough() -> None:
    assert _render("%pad{$track,2}", {"track": "12"}) == "12"


def test_pad_zero_width() -> None:
    assert _render("%pad{$track,0}", {"track": "7"}) == "7"


# --- %default (muzilla addition) ----------------------------------------------


def test_default_uses_fallback_when_empty() -> None:
    assert _render("%default{$missing,N/A}", {}) == "N/A"


def test_default_uses_value_when_present() -> None:
    assert _render("%default{$title,N/A}", {"title": "x"}) == "x"


# --- %sanitize (muzilla addition) ---------------------------------------------


def test_sanitize_replaces_reserved_chars() -> None:
    assert _render("%sanitize{$t}", {"t": "AC/DC"}) == "AC_DC"


# --- %aunique / %sunique ------------------------------------------------------


class _StubResolver:
    def __init__(self, mapping: dict[str, str | None]) -> None:
        self._mapping = mapping

    def resolve(self, key: str) -> str | None:
        return self._mapping.get(key)


def test_aunique_no_collision_renders_empty() -> None:
    resolver = _StubResolver({})
    result = _render(
        "%aunique{}",
        {"albumartist": "X", "album": "Y"},
        resolver=resolver,
    )
    assert result == ""


def test_aunique_collision_resolved_by_year() -> None:
    # The resolver returns which FIELD separates the collision ("year"),
    # not the value itself -- %aunique looks up this item's own value
    # for that field from ctx.values.
    key = "X\x1fY"
    resolver = _StubResolver({key: "year"})
    result = _render(
        "%aunique{}",
        {"albumartist": "X", "album": "Y", "year": 1999},
        resolver=resolver,
    )
    assert result == " [1999]"


def test_aunique_no_resolver_raises_render_error() -> None:
    tmpl = parse("%aunique{}")
    compiled = compile_template(tmpl, source="%aunique{}")
    with pytest.raises(RenderError):
        compiled(RenderContext(values={"albumartist": "X", "album": "Y"}))


def test_sunique_uses_artist_title_key() -> None:
    key = "X\x1fY"
    resolver = _StubResolver({key: "year"})
    result = _render(
        "%sunique{}",
        {"artist": "X", "title": "Y", "year": 1999},
        resolver=resolver,
    )
    assert result == " [1999]"
