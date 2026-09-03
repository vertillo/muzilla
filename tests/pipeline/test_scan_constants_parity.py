"""Parity test: scan_constants is the single source for audio/sidecar/ignore sets.

pipeline/scan and services/import_scope must not drift from it."""

from __future__ import annotations


def test_scan_constants_single_source() -> None:
    from muzilla.pipeline import scan as scan_mod
    from muzilla.pipeline import scan_constants as sc
    from muzilla.services import import_scope as is_mod

    # scan.py re-exports the shared constants (via alias) — values must match
    assert set(getattr(scan_mod, "AUDIO_EXTENSIONS")) == set(sc.AUDIO_EXTENSIONS)  # noqa: B009
    assert set(getattr(scan_mod, "_IGNORED_SIDECAR_NAMES")) == set(sc.IGNORED_SIDECAR_NAMES)  # noqa: B009
    assert set(getattr(scan_mod, "_DEFAULT_IGNORE_DIR_NAMES")) == set(sc.DEFAULT_IGNORE_DIR_NAMES)  # noqa: B009

    # import_scope's private aliases must also equal the shared source
    assert set(getattr(is_mod, "_AUDIO_EXTENSIONS")) == set(sc.AUDIO_EXTENSIONS)  # noqa: B009
    assert set(getattr(is_mod, "_IGNORED_SIDECAR_NAMES")) == set(sc.IGNORED_SIDECAR_NAMES)  # noqa: B009
    assert set(getattr(is_mod, "_DEFAULT_IGNORE_DIR_NAMES")) == set(sc.DEFAULT_IGNORE_DIR_NAMES)  # noqa: B009

    # Sanity: expected canonical values (catch accidental rename drift)
    assert ".mp3" in sc.AUDIO_EXTENSIONS
    assert ".flac" in sc.AUDIO_EXTENSIONS
    assert "cover.jpg" in sc.IGNORED_SIDECAR_NAMES
    assert ".git" in sc.DEFAULT_IGNORE_DIR_NAMES

    # The shared sets are frozenset (immutable single source)
    assert isinstance(sc.AUDIO_EXTENSIONS, frozenset)
    assert isinstance(sc.IGNORED_SIDECAR_NAMES, frozenset)
    assert isinstance(sc.DEFAULT_IGNORE_DIR_NAMES, frozenset)
