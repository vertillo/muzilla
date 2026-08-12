from __future__ import annotations

import shutil
from pathlib import Path

import mutagen
import pytest

from muzilla.tags.reader import read_lyrics, read_track
from muzilla.tags.writer import (
    TagWriteError,
    clear_art,
    clear_lyrics,
    write_art,
    write_fields,
    write_lyrics,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"

FORMATS = ["mp3", "flac", "ogg", "opus", "m4a", "wav", "aiff"]

_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-ish payload for testing" * 10
_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"png-ish payload for testing" * 10


def _copy_fixture(fmt: str, tmp_path: Path) -> Path:
    path = tmp_path / f"test.{fmt}"
    shutil.copy(FIXTURES / f"silence.{fmt}", path)
    return path


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_art_embeds_and_is_detected_by_reader(fmt: str, tmp_path: Path) -> None:
    path = _copy_fixture(fmt, tmp_path)
    write_art(path, _FAKE_JPEG, "image/jpeg")

    meta = read_track(path)
    assert meta.has_embedded_art is True


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_art_preserves_existing_tags(fmt: str, tmp_path: Path) -> None:
    path = _copy_fixture(fmt, tmp_path)
    before = read_track(path)

    write_art(path, _FAKE_JPEG, "image/jpeg")

    after = read_track(path)
    assert after.title == before.title
    assert after.artist == before.artist
    assert after.album == before.album


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_art_replaces_existing_picture_rather_than_appending(fmt: str, tmp_path: Path) -> None:
    path = _copy_fixture(fmt, tmp_path)
    write_art(path, _FAKE_JPEG, "image/jpeg")
    write_art(path, _FAKE_PNG, "image/png")

    audio = mutagen.File(path, easy=False)
    if hasattr(audio, "pictures"):
        assert len(audio.pictures) == 1
        assert audio.pictures[0].data == _FAKE_PNG
    elif "covr" in (audio.tags or {}):
        assert len(audio.tags["covr"]) == 1
        assert bytes(audio.tags["covr"][0]) == _FAKE_PNG


@pytest.mark.parametrize("fmt", FORMATS)
def test_clear_art_removes_embedded_picture(fmt: str, tmp_path: Path) -> None:
    path = _copy_fixture(fmt, tmp_path)
    write_art(path, _FAKE_JPEG, "image/jpeg")
    assert read_track(path).has_embedded_art is True

    clear_art(path)

    assert read_track(path).has_embedded_art is False


@pytest.mark.parametrize("fmt", FORMATS)
def test_clear_art_on_file_with_no_art_is_a_noop(fmt: str, tmp_path: Path) -> None:
    path = _copy_fixture(fmt, tmp_path)
    assert read_track(path).has_embedded_art is False

    clear_art(path)  # must not raise

    assert read_track(path).has_embedded_art is False


def test_write_art_missing_file_raises_tag_write_error(tmp_path: Path) -> None:
    with pytest.raises(TagWriteError):
        write_art(tmp_path / "does-not-exist.mp3", _FAKE_JPEG, "image/jpeg")


def test_write_fields_still_rejects_read_only_fields(tmp_path: Path) -> None:
    path = _copy_fixture("flac", tmp_path)
    with pytest.raises(ValueError, match="read-only"):
        write_fields(path, {"duration_ms": 5000})


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_fields_year_preserves_existing_month_day(fmt: str, tmp_path: Path) -> None:
    """The fixtures carry date=1999-06-12; a bare year edit should keep
    the month/day, matching what §11f's fix to _year_to_date already
    established, now re-verified after §11m's redesign of how the
    current date is obtained."""
    path = _copy_fixture(fmt, tmp_path)
    write_fields(path, {"year": 2005})
    assert read_track(path).date == "2005-06-12"


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_fields_explicit_date_wins_over_same_call_year(fmt: str, tmp_path: Path) -> None:
    """Regression test for §11m (docs/product-spec.md): a `year` write used to
    unconditionally translate into a `date` write and clobber an
    explicit `date` present in the *same* write_fields call — the
    year-to-date translation read the file's *current* (pre-write)
    date, so an accepted ChangeSet containing both an explicit date
    edit and a year edit for one track would silently lose the date
    edit, with the DB (which recorded the accepted date change) left
    disagreeing with the file. The explicit date must win; the year
    translation is skipped entirely when date is also present."""
    path = _copy_fixture(fmt, tmp_path)
    write_fields(path, {"year": 2005, "date": "1998-03-04"})
    assert read_track(path).date == "1998-03-04"


def test_write_fields_year_only_clears_date_when_year_is_none(tmp_path: Path) -> None:
    path = _copy_fixture("flac", tmp_path)
    write_fields(path, {"year": None})
    assert read_track(path).date is None


def test_write_fields_year_read_failure_raises_instead_of_silently_truncating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for §11m (docs/product-spec.md): the pre-fix code wrapped
    the current-date read in a bare `except Exception` and fell back to
    a bare 4-digit year on any failure, permanently discarding existing
    month/day precision with no error, no journal entry, and no
    warning. A read failure must now raise TagWriteError instead — a
    real problem with the file should look like one, not silently
    corrupt an unrelated field's precision."""
    import muzilla.tags.writer as writer_module

    path = _copy_fixture("flac", tmp_path)

    def _boom(_path: object) -> None:
        raise RuntimeError("simulated read failure")

    monkeypatch.setattr(writer_module, "read_track", _boom)
    with pytest.raises(TagWriteError):
        write_fields(path, {"year": 2005})


_LYRICS_TEXT = "Line one\nLine two\nLine three"


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_lyrics_round_trips(fmt: str, tmp_path: Path) -> None:
    path = _copy_fixture(fmt, tmp_path)
    write_lyrics(path, _LYRICS_TEXT)

    assert read_lyrics(path) == _LYRICS_TEXT
    assert read_track(path).has_lyrics is True


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_lyrics_preserves_existing_tags(fmt: str, tmp_path: Path) -> None:
    path = _copy_fixture(fmt, tmp_path)
    before = read_track(path)

    write_lyrics(path, _LYRICS_TEXT)

    after = read_track(path)
    assert after.title == before.title
    assert after.artist == before.artist


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_lyrics_replaces_existing_value(fmt: str, tmp_path: Path) -> None:
    path = _copy_fixture(fmt, tmp_path)
    write_lyrics(path, "old lyrics")
    write_lyrics(path, "new lyrics")

    assert read_lyrics(path) == "new lyrics"


@pytest.mark.parametrize("fmt", FORMATS)
def test_clear_lyrics_removes_value(fmt: str, tmp_path: Path) -> None:
    path = _copy_fixture(fmt, tmp_path)
    write_lyrics(path, _LYRICS_TEXT)
    assert read_lyrics(path) is not None

    clear_lyrics(path)

    assert read_lyrics(path) is None
    assert read_track(path).has_lyrics is False


@pytest.mark.parametrize("fmt", FORMATS)
def test_clear_lyrics_on_file_with_none_is_a_noop(fmt: str, tmp_path: Path) -> None:
    path = _copy_fixture(fmt, tmp_path)
    assert read_lyrics(path) is None

    clear_lyrics(path)  # must not raise

    assert read_lyrics(path) is None


def test_write_lyrics_missing_file_raises_tag_write_error(tmp_path: Path) -> None:
    with pytest.raises(TagWriteError):
        write_lyrics(tmp_path / "does-not-exist.mp3", _LYRICS_TEXT)
