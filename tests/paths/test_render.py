from __future__ import annotations

from muzilla.paths.context import RenderContext
from muzilla.paths.render import compile_and_render, track_to_variables

# --- track_to_variables ------------------------------------------------------


def test_track_to_variables_scalar_passthrough() -> None:
    result = track_to_variables({"title": "Abbey Road", "year": 1969})
    assert result == {"title": "Abbey Road", "year": 1969}


def test_track_to_variables_multi_text_joined() -> None:
    result = track_to_variables({"genre": ["Rock", "Pop"]})
    assert result == {"genre": "Rock, Pop"}


def test_track_to_variables_multi_text_empty_list() -> None:
    result = track_to_variables({"genre": []})
    assert result == {"genre": ""}


def test_track_to_variables_multi_text_none() -> None:
    result = track_to_variables({"genre": None})
    assert result == {"genre": None}


def test_track_to_variables_artists_field() -> None:
    result = track_to_variables({"artists": ["A", "B"]})
    assert result == {"artists": "A, B"}


def test_track_to_variables_unknown_field_passthrough() -> None:
    # A field not in domain.fields (e.g. a caller-supplied convenience
    # value) still passes through as long as it's a JSON-scalar type.
    result = track_to_variables({"custom": "value"})
    assert result == {"custom": "value"}


def test_track_to_variables_non_scalar_unknown_field_stringified() -> None:
    result = track_to_variables({"custom": {"nested": 1}})
    assert result == {"custom": "{'nested': 1}"}


# --- compile_and_render -------------------------------------------------------


def test_flat_template_no_separator_succeeds() -> None:
    ctx = RenderContext(values={"artist": "A", "title": "B"})
    result = compile_and_render("$artist - $title", ctx, create_directories=False)
    assert result.path == "A - B"
    assert result.components == ("A - B",)
    assert result.errors == ()


def test_separator_under_flat_mode_is_a_validation_error() -> None:
    ctx = RenderContext(values={"albumartist": "A", "album": "B"})
    result = compile_and_render(
        "$albumartist/$album", ctx, create_directories=False
    )
    assert result.errors
    assert "create_directories is disabled" in result.errors[0]


def test_separator_under_foldered_mode_splits_into_components() -> None:
    ctx = RenderContext(values={"albumartist": "A", "album": "B"})
    result = compile_and_render(
        "$albumartist/$album", ctx, create_directories=True
    )
    assert result.errors == ()
    assert result.components == ("A", "B")
    assert result.path == "A/B"


def test_field_value_containing_separator_is_flagged_in_flat_mode() -> None:
    # A field value like "AC/DC" containing a literal '/' is treated
    # exactly like a template-literal '/' in flat mode -- the
    # "a rendered '/' is a validation error" check operates on the
    # final rendered string, not just the template's own structure,
    # since an unflagged '/' from field data would be just as
    # surprising (and filesystem-dangerous) as one the template wrote.
    ctx = RenderContext(values={"albumartist": "AC/DC", "album": "B"})
    result = compile_and_render(
        "$albumartist - $album", ctx, create_directories=False
    )
    assert result.errors
    assert "create_directories is disabled" in result.errors[0]


def test_field_value_containing_separator_becomes_extra_directory_in_foldered_mode() -> None:
    # Same input under create_directories=True: the stray '/' becomes a
    # real split point, and each resulting component is independently
    # sanitized (reserved chars, trailing dots, etc.) just like a
    # template-literal separator's components would be.
    ctx = RenderContext(values={"albumartist": "AC/DC", "album": "B"})
    result = compile_and_render(
        "$albumartist - $album", ctx, create_directories=True
    )
    assert result.errors == ()
    assert result.components == ("AC", "DC - B")


def test_empty_rendered_component_is_an_error() -> None:
    ctx = RenderContext(values={})
    result = compile_and_render("$missing/$also_missing", ctx, create_directories=True)
    assert result.errors
    assert "empty path component" in result.errors[0]


def test_render_error_surfaces_as_result_error_not_exception() -> None:
    ctx = RenderContext(values={"d": "not-a-date"})
    result = compile_and_render("%time{$d,%%Y}", ctx, create_directories=False)
    assert result.errors
    assert result.path == ""


def test_replacements_applied_during_sanitization() -> None:
    ctx = RenderContext(values={"title": "hello world"})
    result = compile_and_render(
        "$title", ctx, create_directories=False, replacements=(("o", "0"),)
    )
    assert result.path == "hell0 w0rld"
