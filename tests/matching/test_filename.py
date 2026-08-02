from __future__ import annotations

from muzilla.matching.filename import parse_filename


def test_parse_artist_title_filename_with_high_confidence() -> None:
    parsed = parse_filename("01. Piki - Twilight Twilight.flac")
    assert parsed.artist == "Piki"
    assert parsed.title == "Twilight Twilight"
    assert parsed.confidence == 0.95


def test_parse_filename_keeps_ambiguous_name_as_low_confidence_title() -> None:
    parsed = parse_filename("Twilight Twilight.mp3")
    assert parsed.artist is None
    assert parsed.title == "Twilight Twilight"
    assert parsed.confidence < 0.5
