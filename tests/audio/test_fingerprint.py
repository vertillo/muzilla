from __future__ import annotations

import math
import shutil
import sys
import wave
from array import array
from pathlib import Path

import pytest

from muzilla.audio.fingerprint import FingerprintError, compute_fingerprint

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"

_HAS_FPCALC = shutil.which("fpcalc") is not None

requires_fpcalc = pytest.mark.skipif(
    not _HAS_FPCALC, reason="fpcalc not installed (present in the Docker image, not local dev)"
)


def _write_long_tone(path: Path) -> None:
    sample_rate = 44_100
    samples = array(
        "h",
        (
            round(0.3 * 32_767 * math.sin(2 * math.pi * 440 * sample / sample_rate))
            for sample in range(sample_rate * 30)
        ),
    )
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(samples.tobytes())


@requires_fpcalc
def test_compute_fingerprint_returns_duration_and_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "tone.wav"
    _write_long_tone(path)

    result = compute_fingerprint(path)

    assert result.duration_s >= 30
    assert result.fingerprint


def test_compute_fingerprint_forces_fpcalc_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import acoustid

    calls: list[tuple[str, bool]] = []

    def _fingerprint_file(path: str, *, force_fpcalc: bool = False) -> tuple[float, bytes]:
        calls.append((path, force_fpcalc))
        return 30.0, b"test-fingerprint"

    monkeypatch.setattr(acoustid, "fingerprint_file", _fingerprint_file)

    result = compute_fingerprint(Path("/music/track.mp3"))

    assert calls == [("/music/track.mp3", True)]
    assert result.fingerprint == "test-fingerprint"


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
