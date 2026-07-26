"""Normalization for external identifiers found in tags or returned by providers.

Pure functions only — no I/O. Malformed input returns None rather than
raising, since tag data is untrusted and a bad ISRC in a file must not
crash a scan.
"""

from __future__ import annotations

import re

_MBID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_ISRC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{2}\d{5}$")
_BARCODE_RE = re.compile(r"^\d{8,14}$")


def normalize_mbid(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    return v if _MBID_RE.match(v) else None


def normalize_isrc(value: str | None) -> str | None:
    if not value:
        return None
    v = re.sub(r"[\s-]", "", value.strip()).upper()
    return v if _ISRC_RE.match(v) else None


def normalize_barcode(value: str | None) -> str | None:
    if not value:
        return None
    v = re.sub(r"[\s-]", "", value.strip())
    return v if _BARCODE_RE.match(v) else None
