"""Conservative filename parsing used only to recover missing tag evidence.

The parser deliberately recognizes a small, common grammar instead of guessing at
every filename.  Matching can safely use a high-confidence ``Artist - Title``
split when tags are absent, while ambiguous names remain title-only evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_LEADING_INDEX = re.compile(r"^\s*(?:\d{1,3}[ ._-]+)?")
_SEPARATOR = re.compile(r"\s+-\s+")


@dataclass(frozen=True, slots=True)
class ParsedFilename:
    artist: str | None
    title: str | None
    confidence: float
    """Confidence in the filename interpretation, not match confidence."""


def parse_filename(filename: str) -> ParsedFilename:
    """Extract a conservative artist/title pair from a basename.

    A spaced dash avoids treating punctuation within a title as a separator.  A
    remaining non-empty name is useful as a low-confidence title fallback, but
    never invents an artist.
    """
    stem = _LEADING_INDEX.sub("", Path(filename).stem).strip()
    if not stem:
        return ParsedFilename(artist=None, title=None, confidence=0.0)

    parts = _SEPARATOR.split(stem, maxsplit=1)
    if len(parts) == 2:
        artist, title = (part.strip() for part in parts)
        if artist and title:
            return ParsedFilename(artist=artist, title=title, confidence=0.95)
    return ParsedFilename(artist=None, title=stem, confidence=0.45)


__all__ = ["ParsedFilename", "parse_filename"]
