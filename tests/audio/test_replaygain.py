from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from muzilla.audio.replaygain import (
    ReplayGainError,
    compute_album_replaygain,
    compute_track_replaygain,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"

_HAS_RSGAIN = shutil.which("rsgain") is not None

requires_rsgain = pytest.mark.skipif(
    not _HAS_RSGAIN, reason="rsgain not installed (present in the Docker image, not local dev)"
)

_TRACK_ONLY_TAB = (
    "File\tLoudness\tGain\tPeak\tPeak dB\tClipping\tClip-adjusted\n"
    "/music/a.flac\t-14.20 LUFS\t-3.20 dB\t0.891234\t-1.00 dB\tNo\tNo\n"
)

_ALBUM_TAB = (
    "File\tLoudness\tGain\tPeak\tPeak dB\tClipping\tClip-adjusted"
    "\tAlbum Loudness\tAlbum Gain\tAlbum Peak\n"
    "/music/a.flac\t-14.20 LUFS\t-3.20 dB\t0.891234\t-1.00 dB\tNo\tNo"
    "\t-13.50 LUFS\t-2.50 dB\t0.950000\n"
    "/music/b.flac\t-15.00 LUFS\t-4.00 dB\t0.800000\t-1.94 dB\tNo\tNo"
    "\t-13.50 LUFS\t-2.50 dB\t0.950000\n"
)


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["rsgain"], returncode=0, stdout=stdout, stderr="")


@requires_rsgain
def test_compute_track_replaygain_real_binary() -> None:
    result = compute_track_replaygain(FIXTURES / "silence.mp3")
    assert isinstance(result.track_gain_db, float)
    assert isinstance(result.track_peak, float)


def test_compute_track_replaygain_parses_tab_output() -> None:
    with (
        patch("muzilla.audio.replaygain.shutil.which", return_value="/usr/bin/rsgain"),
        patch("muzilla.audio.replaygain.subprocess.run", return_value=_completed(_TRACK_ONLY_TAB)),
    ):
        result = compute_track_replaygain(Path("/music/a.flac"))
    assert result.track_gain_db == pytest.approx(-3.20)
    assert result.track_peak == pytest.approx(0.891234)
    assert result.album_gain_db is None
    assert result.album_peak is None


def test_compute_album_replaygain_parses_album_columns() -> None:
    paths = [Path("/music/a.flac"), Path("/music/b.flac")]
    with (
        patch("muzilla.audio.replaygain.shutil.which", return_value="/usr/bin/rsgain"),
        patch("muzilla.audio.replaygain.subprocess.run", return_value=_completed(_ALBUM_TAB)),
    ):
        results = compute_album_replaygain(paths)
    assert set(results) == set(paths)
    a = results[Path("/music/a.flac")]
    assert a.track_gain_db == pytest.approx(-3.20)
    assert a.album_gain_db == pytest.approx(-2.50)
    assert a.album_peak == pytest.approx(0.95)


def test_compute_track_replaygain_missing_binary_raises_clean_error() -> None:
    with (
        patch("muzilla.audio.replaygain.shutil.which", return_value=None),
        pytest.raises(ReplayGainError, match="rsgain binary not found"),
    ):
        compute_track_replaygain(FIXTURES / "silence.mp3")


def test_compute_track_replaygain_nonzero_exit_raises() -> None:
    error = subprocess.CalledProcessError(1, ["rsgain"], output="", stderr="unsupported format")
    with (
        patch("muzilla.audio.replaygain.shutil.which", return_value="/usr/bin/rsgain"),
        patch("muzilla.audio.replaygain.subprocess.run", side_effect=error),
        pytest.raises(ReplayGainError, match="unsupported format"),
    ):
        compute_track_replaygain(Path("/music/bad.flac"))


def test_compute_track_replaygain_no_result_for_path_raises() -> None:
    """rsgain succeeds but the output has no row for the requested path
    (e.g. it silently skipped a corrupt file) — must raise, not return
    None, so callers don't have to null-check on top of exception
    handling."""
    with (
        patch("muzilla.audio.replaygain.shutil.which", return_value="/usr/bin/rsgain"),
        patch("muzilla.audio.replaygain.subprocess.run", return_value=_completed("File\tGain\tPeak\n")),
        pytest.raises(ReplayGainError, match="no result"),
    ):
        compute_track_replaygain(Path("/music/a.flac"))


def test_compute_album_replaygain_empty_paths_returns_empty_dict() -> None:
    assert compute_album_replaygain([]) == {}
