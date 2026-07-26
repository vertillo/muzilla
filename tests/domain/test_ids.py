from __future__ import annotations

import pytest

from muzilla.domain.ids import normalize_barcode, normalize_isrc, normalize_mbid


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("f4a0f0a0-0f0a-0f0a-0f0a-0f0a0f0a0f0a", "f4a0f0a0-0f0a-0f0a-0f0a-0f0a0f0a0f0a"),
        ("F4A0F0A0-0F0A-0F0A-0F0A-0F0A0F0A0F0A", "f4a0f0a0-0f0a-0f0a-0f0a-0f0a0f0a0f0a"),
        (" f4a0f0a0-0f0a-0f0a-0f0a-0f0a0f0a0f0a ", "f4a0f0a0-0f0a-0f0a-0f0a-0f0a0f0a0f0a"),
        ("not-a-uuid", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_mbid(raw: str | None, expected: str | None) -> None:
    assert normalize_mbid(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("USRC17607839", "USRC17607839"),
        ("us-rc1-76-07839", "USRC17607839"),
        ("usrc17607839", "USRC17607839"),
        ("too-short", None),
        (None, None),
    ],
)
def test_normalize_isrc(raw: str | None, expected: str | None) -> None:
    assert normalize_isrc(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0602537351961", "0602537351961"),
        ("0-602537-351961", "0602537351961"),
        ("123", None),
        (None, None),
    ],
)
def test_normalize_barcode(raw: str | None, expected: str | None) -> None:
    assert normalize_barcode(raw) == expected
