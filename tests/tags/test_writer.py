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
