"""Hypothesis property tests for the tag round-trip invariant:

    write(read(f) ⊕ changes) → read → assert changes present ∧ everything else unchanged

Hypothesis is a dev dependency. These properties catch "the
ID3v2.3-vs-2.4 and Vorbis-multi-value bugs that otherwise ship," which
is exactly the class of bug the hand-written matrix tests in
test_writer.py can miss: they exercise fixed example values, not the
edge of the input space (empty strings, very long strings, unicode
combining characters, single-vs-multi-element lists).
"""

from __future__ import annotations

import shutil
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from muzilla.domain.metadata import TrackMeta
from muzilla.tags.reader import read_track
from muzilla.tags.writer import write_fields

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"
FORMATS = ["mp3", "flac", "ogg", "opus", "m4a", "wav", "aiff"]

# Excludes surrogates (mutagen/format containers reject lone surrogates
# outright on some backends), the NUL byte (several tag formats use NUL
# as an internal separator), and newline (confirmed at the mutagen
# level: an ID3 text frame element containing "\n" is truncated at the
# newline on save+reload -- "a\nb" round-trips to "a" -- while "\r" and
# "\t" are unaffected, so this is specifically a newline-as-terminator
# quirk, not general whitespace intolerance). All three are format/
# library limitations, not muzilla bugs worth chasing here.
_TEXT_STRATEGY = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00\n"),
    max_size=500,
)


def _copy_fixture(fmt: str, tmp_path: Path) -> Path:
    path = tmp_path / f"test.{fmt}"
    shutil.copy(FIXTURES / f"silence.{fmt}", path)
    return path


def _assert_unchanged_except(before: TrackMeta, after: TrackMeta, *changed_fields: str) -> None:
    for f in dataclass_fields(TrackMeta):
        if f.name in changed_fields:
            continue
        assert getattr(after, f.name) == getattr(before, f.name), (
            f"field {f.name!r} changed unexpectedly: "
            f"{getattr(before, f.name)!r} -> {getattr(after, f.name)!r}"
        )


@pytest.mark.parametrize("fmt", FORMATS)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=30)
@given(value=_TEXT_STRATEGY)
def test_text_field_round_trips_and_leaves_everything_else_unchanged(
    fmt: str, tmp_path: Path, value: str
) -> None:
    path = _copy_fixture(fmt, tmp_path)
    before = read_track(path)

    write_fields(path, {"title": value})
    after = read_track(path)

    if value == "":
        # Every format here treats writing an empty string as clearing
        # the frame/tag entirely, not storing a zero-length value —
        # confirmed behavior, not a gap, so the invariant for this case
        # is "title reads back as None," not "title reads back as ''".
        assert after.title is None
    else:
        assert after.title == value
    _assert_unchanged_except(before, after, "title")


@pytest.mark.parametrize("fmt", FORMATS)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=20)
@given(value=st.integers(min_value=0, max_value=9999))
def test_int_field_round_trips_and_leaves_everything_else_unchanged(
    fmt: str, tmp_path: Path, value: int
) -> None:
    path = _copy_fixture(fmt, tmp_path)
    before = read_track(path)

    write_fields(path, {"year": value})
    after = read_track(path)

    assert after.year == value
    # "date" legitimately changes alongside "year": tags/writer.py
    # translates a year write into a date write (year is not a mapped
    # tag frame in any format -- see write_fields' docstring), so date's
    # leading 4 digits move with it by design.
    _assert_unchanged_except(before, after, "year", "date")


@pytest.mark.parametrize("fmt", FORMATS)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=10)
@given(value=st.booleans())
def test_bool_field_round_trips_and_leaves_everything_else_unchanged(
    fmt: str, tmp_path: Path, value: bool
) -> None:
    path = _copy_fixture(fmt, tmp_path)
    before = read_track(path)

    write_fields(path, {"compilation": value})
    after = read_track(path)

    assert after.compilation == value
    _assert_unchanged_except(before, after, "compilation")


@pytest.mark.parametrize("fmt", FORMATS)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=20)
@given(
    values=st.lists(
        # A purely-numeric string (e.g. "0") is excluded: on ID3 formats
        # (mp3/wav/aiff), mutagen's own TCON.text property applies the
        # legacy ID3v1 numeric-genre-code table on *read* -- "0" comes
        # back as "Blues", "17" as "Rock", etc. Confirmed at the
        # mutagen level (round-tripped through a bare mutagen.id3.TCON
        # object with no muzilla code involved): the raw string really
        # is written verbatim, there is just no way to read it back out
        # through mutagen's TCON class once saved. A real genre/mood
        # tag is essentially never a bare integer, so this is a narrow,
        # documented exclusion. Newline is already excluded from
        # _TEXT_STRATEGY itself.
        _TEXT_STRATEGY.filter(lambda s: s != "" and not s.isdigit()),
        min_size=0,
        max_size=5,
        unique=True,
    )
)
def test_multi_valued_field_round_trips_and_leaves_everything_else_unchanged(
    fmt: str, tmp_path: Path, values: list[str]
) -> None:
    path = _copy_fixture(fmt, tmp_path)
    before = read_track(path)

    write_fields(path, {"genre": values})
    after = read_track(path)

    assert list(after.genre) == values
    _assert_unchanged_except(before, after, "genre")


@pytest.mark.parametrize("fmt", ["mp3", "wav", "aiff"])
def test_numeric_genre_string_is_a_known_id3_limitation_not_a_muzilla_bug(
    fmt: str, tmp_path: Path
) -> None:
    """Documents the exclusion above with a concrete example, rather
    than leaving it as a silent filter: this is what the Hypothesis
    test actually found before the strategy was narrowed. mutagen's
    TCON.text applies the legacy ID3v1 numeric-genre-code table on
    read; genre="0" is written verbatim but reads back as "Blues"."""
    path = _copy_fixture(fmt, tmp_path)

    write_fields(path, {"genre": ["0"]})
    after = read_track(path)

    assert after.genre == ("Blues",)  # not ("0",) -- see comment above


@pytest.mark.parametrize("fmt", ["mp3", "wav", "aiff"])
def test_newline_in_id3_text_frame_is_a_known_mutagen_limitation(
    fmt: str, tmp_path: Path
) -> None:
    """Same reasoning as the numeric-genre test above, for a different
    ID3-specific finding: a text-frame element containing "\\n" is
    truncated at the newline by mutagen's own save+reload cycle
    ("a\\nb" -> "a") — confirmed with a bare mutagen.id3.TCON object,
    no muzilla code involved. "\\r" and "\\t" are unaffected, so this is
    specifically newline-as-terminator, not general whitespace
    intolerance. _TEXT_STRATEGY excludes "\\n" for this reason."""
    path = _copy_fixture(fmt, tmp_path)

    write_fields(path, {"genre": ["a\nb"]})
    after = read_track(path)

    assert after.genre == ("a",)  # not ("a\nb",) -- see comment above
