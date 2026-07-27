from __future__ import annotations

import io

import pytest
from PIL import Image

from muzilla.audio.art import ArtProcessingError, process_art


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
    assert decoded.mode == "RGBA"


def test_process_art_flattens_opaque_png_to_jpeg() -> None:
    data = _make_opaque_png(300, 300)
    result = process_art(data, max_dimension=1200)
    assert result.mime == "image/jpeg"


def test_process_art_undecodable_data_raises() -> None:
    with pytest.raises(ArtProcessingError):
        process_art(b"not an image, just garbage bytes", max_dimension=1200)


def test_process_art_output_is_decodable() -> None:
    data = _make_jpeg(2000, 2000)
    result = process_art(data, max_dimension=500)
    decoded = Image.open(io.BytesIO(result.data))
    decoded.load()
    assert decoded.width == 500
    assert decoded.height == 500
