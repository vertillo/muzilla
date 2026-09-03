"""Format support classification derived from implementation evidence.

Supported: read/write/Apply/Undo verified via disposable fixtures (mp3, flac, ogg, opus, m4a, wav, aiff).
Best-effort: none currently; placeholder for future verified partial support.
Unsupported: WavPack, WMA/ASF, DSF remain unverified; not advertised as supported and rejected if encountered.
Never infer support from extension alone; verification must prove round-trip.
"""
from __future__ import annotations

SUPPORTED_FORMATS: frozenset[str] = frozenset({"MP3", "FLAC", "OGG", "Opus", "MP4", "WAV", "AIFF"})
BEST_EFFORT_FORMATS: frozenset[str] = frozenset()
UNSUPPORTED_FORMATS: frozenset[str] = frozenset({"WavPack", "WMA", "ASF", "DSF"})

# Extensions that map to unsupported formats (for explicit rejection before mutagen probe)
UNSUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".wv", ".wma", ".asf", ".dsf", ".dff"})

# Human-readable classification for docs/tests
CLASSIFICATION: dict[str, str] = {
    **{fmt: "supported" for fmt in SUPPORTED_FORMATS},
    **{fmt: "best-effort" for fmt in BEST_EFFORT_FORMATS},
    **{fmt: "unsupported" for fmt in UNSUPPORTED_FORMATS},
}


def classify_format(fmt: str | None) -> str:
    if fmt in SUPPORTED_FORMATS:
        return "supported"
    if fmt in BEST_EFFORT_FORMATS:
        return "best-effort"
    if fmt in UNSUPPORTED_FORMATS:
        return "unsupported"
    return "unknown"


def classify_extension(ext: str) -> str:
    ext = ext.lower()
    if ext in UNSUPPORTED_EXTENSIONS:
        return "unsupported"
    # Supported extensions are handled via AUDIO_EXTENSIONS in pipeline/scan.py
    # Any other extension is unknown/unsupported for reviewed write
    return "unknown"
