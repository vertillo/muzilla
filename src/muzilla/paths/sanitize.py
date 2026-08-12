"""Path-component sanitization.

Every function here operates on ONE path component at a time — a single
filename or single directory segment, never a full multi-segment path.
The caller (paths/render.py) is responsible for splitting on '/' first
when create_directories=True; sanitizing per-component (rather than the
whole rendered path at once) is what lets a legitimate '/' separator
survive while stray reserved characters inside one segment don't.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

_RESERVED_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_DEFAULT_REPLACEMENT = "_"
_DEFAULT_MAX_BYTES = 255


def sanitize_component(
    component: str,
    *,
    replacements: Sequence[tuple[str, str]] = (),
    substitute: str = _DEFAULT_REPLACEMENT,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> str:
    """Order matters:
    1. Apply configurable `replace:` regex substitutions (user-provided).
    2. Replace reserved chars (<>:"/\\|?*) and control chars with `substitute`.
    3. NFC-normalize (macOS SMB clients otherwise create duplicate-looking dirs).
    4. Strip trailing dots/spaces (critical for Windows/SMB/NAS) — after
       char replacement, since a literal trailing '.'/' ' that was never
       a reserved char must still go, but a reserved char that got
       replaced with `substitute` shouldn't need re-stripping.
    5. If the base name (case-insensitive, extension-stripped) matches a
       Windows reserved device name, prefix with `substitute`.
    6. Clamp to `max_bytes` UTF-8 bytes without splitting a base+
       combining-mark pair.
    """
    result = component
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result)

    result = _RESERVED_CHARS_RE.sub(substitute, result)
    result = unicodedata.normalize("NFC", result)
    result = result.rstrip(". ")

    if _is_windows_reserved(result):
        result = f"{substitute}{result}"

    result = _clamp_bytes(result, max_bytes)
    return result


def _is_windows_reserved(component: str) -> bool:
    base = component.rsplit(".", 1)[0] if "." in component else component
    return base.upper() in _WINDOWS_RESERVED


def _clamp_bytes(component: str, max_bytes: int) -> str:
    encoded = component.encode("utf-8")
    if len(encoded) <= max_bytes:
        return component

    truncated = encoded[:max_bytes]
    # Back off until we land on a valid UTF-8 boundary.
    while truncated:
        try:
            text = truncated.decode("utf-8")
            break
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    else:
        return ""

    # Never leave a combining mark stranded without its base character —
    # back up further if the last character combines with what precedes it.
    while text and unicodedata.combining(text[-1]):
        text = text[:-1]
    return text
