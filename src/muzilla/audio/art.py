"""Art resize/validate (docs/PLAN.md §Phase-6 — "embedded primarily").

Sync, CPU-bound Pillow work — same "async only at the edges" shape as
`audio/replaygain.py` and `audio/fingerprint.py`: fetching the raw
bytes is the network half (an `ArtProvider`, called from async job/
pipeline code), and this module only ever operates on bytes already in
memory. Resize keeps embedded art from bloating every file at library
scale — a flat folder with 50k+ tracks can't afford full-resolution
Cover Art Archive scans embedded verbatim in each one.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import cast

from PIL import Image, UnidentifiedImageError

_SUPPORTED_OUTPUT_MIMES = {"image/jpeg": "JPEG", "image/png": "PNG"}


class ArtProcessingError(Exception):
    """Raised when the fetched bytes aren't a decodable image, or
    resizing otherwise fails — callers should catch this per-track and
    continue, never let one bad fetch abort a bulk enrichment job."""


@dataclass(frozen=True, slots=True)
class ProcessedArt:
    data: bytes
    mime: str
    width: int
    height: int


def process_art(data: bytes, *, max_dimension: int) -> ProcessedArt:
    """Decodes `data`, downscales so neither edge exceeds `max_dimension`
    (upscaling never happens — a smaller source image is left as-is),
    and re-encodes as JPEG (or PNG if the source had transparency, which
    a JPEG re-encode would silently flatten to black)."""
    try:
        opened = Image.open(io.BytesIO(data))
        opened.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ArtProcessingError(f"undecodable image data: {exc}") from exc

    has_alpha = opened.mode in ("RGBA", "LA", "P") and _has_transparency(opened)
    image: Image.Image
    if has_alpha:
        image = opened.convert("RGBA")
        output_mime = "image/png"
    else:
        image = opened.convert("RGB")
        output_mime = "image/jpeg"

    if image.width > max_dimension or image.height > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    try:
        image.save(buffer, format=_SUPPORTED_OUTPUT_MIMES[output_mime], quality=90)
    except Exception as exc:
        raise ArtProcessingError(f"failed to encode processed image: {exc}") from exc

    return ProcessedArt(
        data=buffer.getvalue(), mime=output_mime, width=image.width, height=image.height
    )


def _has_transparency(image: Image.Image) -> bool:
    if image.mode == "P":
        return bool("transparency" in image.info)
    if image.mode in ("RGBA", "LA"):
        alpha = image.getchannel("A")
        min_value, _ = cast("tuple[int, int]", alpha.getextrema())
        return bool(min_value < 255)
    return False
