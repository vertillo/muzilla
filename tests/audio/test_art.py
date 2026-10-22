from __future__ import annotations

import io

import pytest
from PIL import Image

from muzilla.audio.art import (
    ArtMediaTypeError,
    ArtProcessingError,
    ArtSizeError,
    process_art,
    process_uploaded_art,
)


def _make_jpeg(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), color=(200, 50, 50))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _make_transparent_png(width: int, height: int) -> bytes:
    image = Image.new("RGBA", (width, height), color=(0, 0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _make_opaque_png(width: int, height: int) -> bytes:
    image = Image.new("RGBA", (width, height), color=(10, 20, 30, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_process_art_leaves_small_image_unresized() -> None:
    data = _make_jpeg(300, 300)
    result = process_art(data, max_dimension=1200)
    assert result.width == 300
    assert result.height == 300
    assert result.mime == "image/jpeg"


def test_process_art_downscales_oversized_image() -> None:
    data = _make_jpeg(3000, 1500)
    result = process_art(data, max_dimension=1200)
    assert result.width == 1200
    assert result.height == 600  # aspect ratio preserved


def test_process_art_never_upscales() -> None:
    data = _make_jpeg(100, 100)
    result = process_art(data, max_dimension=1200)
    assert result.width == 100
    assert result.height == 100


def test_process_art_preserves_transparency_as_png() -> None:
    data = _make_transparent_png(300, 300)
    result = process_art(data, max_dimension=1200)
    assert result.mime == "image/png"

    decoded = Image.open(io.BytesIO(result.data))
    decoded.load()
    assert decoded.mode == "RGBA"
    assert decoded.getchannel("A").getpixel((0, 0)) == 0


@pytest.mark.parametrize("mode", ["LA", "P"])
def test_process_art_preserves_transparency_for_non_rgba_modes(mode: str) -> None:
    image = Image.new(mode, (10, 10), color=(128, 0) if mode == "LA" else 0)
    if mode == "P":
        image.info["transparency"] = 0
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    result = process_art(buffer.getvalue(), max_dimension=1200)
    decoded = Image.open(io.BytesIO(result.data))
    decoded.load()
    assert result.mime == "image/png"
    assert decoded.mode == "RGBA"
    assert decoded.getchannel("A").getpixel((0, 0)) == 0


def test_process_art_flattens_opaque_png_to_jpeg() -> None:
    data = _make_opaque_png(300, 300)
    result = process_art(data, max_dimension=1200)
    assert result.mime == "image/jpeg"
    decoded = Image.open(io.BytesIO(result.data))
    decoded.load()
    assert decoded.mode == "RGB"


def test_process_art_undecodable_data_raises() -> None:
    with pytest.raises(ArtProcessingError):
        process_art(b"not an image, just garbage bytes", max_dimension=1200)


def test_process_art_rejects_truncated_jpeg() -> None:
    with pytest.raises(ArtProcessingError):
        process_art(_make_jpeg(20, 20)[:-20], max_dimension=1200)


@pytest.mark.parametrize("max_pixels", [5, 4], ids=["warning", "error"])
def test_process_art_translates_decompression_bomb_limits(
    max_pixels: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", max_pixels)
    with pytest.raises(ArtSizeError, match="decode safety limit"):
        process_art(_make_jpeg(3, 3), max_dimension=1200)


def test_process_art_output_is_decodable() -> None:
    data = _make_jpeg(2000, 2000)
    result = process_art(data, max_dimension=500)
    decoded = Image.open(io.BytesIO(result.data))
    decoded.load()
    assert decoded.width == 500
    assert decoded.height == 500


def test_uploaded_art_requires_declared_mime_to_match_decoded_format() -> None:
    with pytest.raises(ArtMediaTypeError, match="does not match"):
        process_uploaded_art(
            _make_transparent_png(300, 300),
            declared_mime="image/jpeg",
            max_dimension=1200,
            max_source_dimension=4096,
            max_source_pixels=16_777_216,
        )


def test_uploaded_art_rejects_source_dimensions_before_full_decode() -> None:
    with pytest.raises(ArtSizeError, match="dimensions"):
        process_uploaded_art(
            _make_jpeg(5000, 1),
            declared_mime="image/jpeg",
            max_dimension=1200,
            max_source_dimension=4096,
            max_source_pixels=16_777_216,
        )
