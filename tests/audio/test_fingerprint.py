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


def test_compute_fingerprint_raises_clean_error_without_fpcalc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministic fail-closed coverage for a missing fpcalc binary.

    Forces fpcalc unavailability at the acoustid execution seam so the test
    holds regardless of host tooling (CI installs ffmpeg, which supplies
    fpcalc and previously defeated the host-dependent skipif). Production
    behavior is unchanged: the test only substitutes the seam to raise the
    same ``NoBackendError("fpcalc not found")`` the backend raises when the
    binary is absent, then asserts it surfaces as a clean ``FingerprintError``.
    """
    import acoustid

    def _missing_fpcalc(*args: object, **kwargs: object) -> object:
        raise acoustid.NoBackendError("fpcalc not found")

    monkeypatch.setattr(acoustid, "fingerprint_file", _missing_fpcalc)
    with pytest.raises(FingerprintError, match="fpcalc"):
        compute_fingerprint(FIXTURES / "silence.mp3")
