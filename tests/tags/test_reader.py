from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from muzilla.tags.reader import TagReadError, read_track
from muzilla.tags.writer import write_art

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"

FORMATS = ["mp3", "flac", "ogg", "opus", "m4a", "wav", "aiff"]


@pytest.mark.parametrize("fmt", FORMATS)
def test_reads_common_fields(fmt: str) -> None:
    meta = read_track(FIXTURES / f"silence.{fmt}")

    assert meta.title == "Ágætis byrjun"
    assert meta.artist == "Sigur Rós"
    assert meta.album == "Ágætis byrjun"
    assert meta.album_artist == "Sigur Rós"
    assert meta.composer == "Jónsi"
    assert meta.track_no == 9
    assert meta.disc_no == 1
    assert meta.year == 1999
    assert meta.genre == ("Post-Rock",)
    assert meta.bpm == 72
    assert meta.isrc == "GBUM71029604"
    assert meta.label == "PLAY IT AGAIN SAM"
    assert meta.mb_release_id == "f4a0f0a0-0f0a-0f0a-0f0a-0f0a0f0a0f0a"


@pytest.mark.parametrize("fmt", FORMATS)
def test_probe_fields_populated(fmt: str) -> None:
    meta = read_track(FIXTURES / f"silence.{fmt}")

    assert meta.duration_ms is not None
    assert 900 <= meta.duration_ms <= 1100  # ~1 second fixture, allow codec slack
    assert meta.codec is not None
    # sample_rate/bitrate/channels vary by codec; just assert they're set
    assert meta.sample_rate is not None or meta.bitrate is not None


@pytest.mark.parametrize("fmt", FORMATS)
def test_track_total_where_supported(fmt: str) -> None:
    meta = read_track(FIXTURES / f"silence.{fmt}")
    # Opus/Vorbis TRACKTOTAL and MP4 trkn both carry the total; ID3 TRCK
    # encodes it as "n/total" and is split by the reader.
    assert meta.track_total == 10


def test_missing_file_raises_tag_read_error(tmp_path: Path) -> None:
    with pytest.raises(TagReadError):
        read_track(tmp_path / "does-not-exist.mp3")


def test_corrupt_file_raises_tag_read_error(tmp_path: Path) -> None:
    bad = tmp_path / "corrupt.mp3"
    bad.write_bytes(b"this is not an mp3 file, just garbage bytes" * 10)
    with pytest.raises(TagReadError):
        read_track(bad)


@pytest.mark.parametrize("fmt", FORMATS)
def test_has_embedded_art_false_for_fixtures_without_art(fmt: str) -> None:
    # The committed fixtures carry tags but no embedded picture.
    meta = read_track(FIXTURES / f"silence.{fmt}")
    assert meta.has_embedded_art is False


@pytest.mark.parametrize("fmt", FORMATS)
def test_has_embedded_art_true_after_writer_embeds_a_picture(fmt: str, tmp_path: Path) -> None:
    path = tmp_path / f"with_art.{fmt}"
    shutil.copy(FIXTURES / f"silence.{fmt}", path)
    write_art(path, b"\xff\xd8\xff\xe0" + b"0" * 100, "image/jpeg")

    meta = read_track(path)
    assert meta.has_embedded_art is True


def test_id3_multi_text_filters_empty_elements() -> None:
    """Regression test for §11m (docs/product-spec.md): ID3v2.4 stores
    multi-values null-separated, and many real-world taggers emit a
    trailing null — a bare `mutagen.id3.TCON` (no muzilla code involved
    in constructing it, isolating this as ID3/mutagen behavior rather
    than a muzilla write-path bug, same technique as §11f's mutagen
    limitation investigations) with `text=["Rock", ""]` must read back
    as `("Rock",)`, not `("Rock", "")` — the empty element isn't a real
    genre value and would otherwise leak into the DB as a spurious
    drift diff against providers that return no such value."""
    from mutagen.id3 import ID3, TCON, Encoding

    from muzilla.tags.reader import _id3_multi_text

    tags = ID3()
    tags.setall("TCON", [TCON(encoding=Encoding.UTF8, text=["Rock", ""])])
    assert _id3_multi_text(tags, "TCON") == ("Rock",)

    tags_empty_only = ID3()
    tags_empty_only.setall("TCON", [TCON(encoding=Encoding.UTF8, text=[""])])
    assert _id3_multi_text(tags_empty_only, "TCON") == ()
