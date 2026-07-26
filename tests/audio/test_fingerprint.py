from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from muzilla.audio.fingerprint import FingerprintError, compute_fingerprint

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"

_HAS_FPCALC = shutil.which("fpcalc") is not None

requires_fpcalc = pytest.mark.skipif(
    not _HAS_FPCALC, reason="fpcalc not installed (present in the Docker image, not local dev)"
)


@requires_fpcalc
def test_compute_fingerprint_returns_duration_and_fingerprint() -> None:
    result = compute_fingerprint(FIXTURES / "silence.mp3")
    assert result.duration_s > 0
    assert isinstance(result.fingerprint, str)
    assert len(result.fingerprint) > 0


def test_compute_fingerprint_missing_file_raises_fingerprint_error() -> None:
    with pytest.raises(FingerprintError):
        compute_fingerprint(FIXTURES / "does-not-exist.mp3")


@pytest.mark.skipif(_HAS_FPCALC, reason="only meaningful when fpcalc is actually absent")
def test_compute_fingerprint_raises_clean_error_without_fpcalc() -> None:
    with pytest.raises(FingerprintError, match="fpcalc"):
        compute_fingerprint(FIXTURES / "silence.mp3")
