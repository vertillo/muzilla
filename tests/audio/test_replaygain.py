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
    "Filename\tLoudness (LUFS)\tGain (dB)\tPeak\t Peak (dB)\tPeak Type"
    "\tClipping Adjustment?\n"
    "0.flac\t-14.20\t-3.20\t0.891234\t-1.00\tSample\tN\n"
)

_ALBUM_TAB = (
    "Filename\tLoudness (LUFS)\tGain (dB)\tPeak\t Peak (dB)\tPeak Type"
    "\tClipping Adjustment?\n"
    "0.flac\t-14.20\t-3.20\t0.891234\t-1.00\tSample\tN\n"
    "1.flac\t-15.00\t-4.00\t0.800000\t-1.94\tSample\tN\n"
    "Album\t-13.50\t-2.50\t0.950000\t-0.45\tSample\tN\n"
)


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["rsgain"], returncode=0, stdout=stdout, stderr="")


@requires_rsgain
def test_compute_track_replaygain_real_binary() -> None:
    result = compute_track_replaygain(FIXTURES / "silence.mp3")
    assert isinstance(result.track_gain_db, float)
    assert isinstance(result.track_peak, float)


def test_compute_track_replaygain_parses_v34_output_and_uses_scan_only_mode() -> None:
    alias_paths: list[Path] = []

    def _run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[1:5] == ["custom", "-O", "-s", "s"]
        assert "tab" not in args
        alias_paths.extend(Path(argument) for argument in args[5:])
        assert len(alias_paths) == 1 and alias_paths[0].is_symlink()
        return _completed(_TRACK_ONLY_TAB)

    with (
        patch("muzilla.audio.replaygain.shutil.which", return_value="/usr/bin/rsgain"),
        patch("muzilla.audio.replaygain.subprocess.run", side_effect=_run),
    ):
        result = compute_track_replaygain(Path("/music/a.flac"))

    assert result.path == Path("/music/a.flac")
    assert result.track_gain_db == pytest.approx(-3.20)
    assert result.track_peak == pytest.approx(0.891234)
    assert result.album_gain_db is None
    assert result.album_peak is None
    assert all(not path.exists() and not path.is_symlink() for path in alias_paths)


def test_compute_album_replaygain_maps_duplicate_basenames_and_summary_row() -> None:
    paths = [Path("/music/disc1/01.flac"), Path("/music/disc2/01.flac")]
    aliases: list[Path] = []

    def _run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[1:6] == ["custom", "-O", "-s", "s", "-a"]
        aliases.extend(Path(argument) for argument in args[6:])
        assert [path.name for path in aliases] == ["0.flac", "1.flac"]
        assert all(path.is_symlink() for path in aliases)
        return _completed(_ALBUM_TAB)

    with (
        patch("muzilla.audio.replaygain.shutil.which", return_value="/usr/bin/rsgain"),
        patch("muzilla.audio.replaygain.subprocess.run", side_effect=_run),
    ):
        results = compute_album_replaygain(paths)

    assert set(results) == set(paths)
    assert results[paths[0]].track_gain_db == pytest.approx(-3.20)
    assert results[paths[1]].track_gain_db == pytest.approx(-4.00)
    assert all(result.album_gain_db == pytest.approx(-2.50) for result in results.values())
    assert all(result.album_peak == pytest.approx(0.95) for result in results.values())
    assert all(not path.exists() and not path.is_symlink() for path in aliases)


def test_compute_track_replaygain_rejects_duplicate_output_rows() -> None:
    output = _TRACK_ONLY_TAB + _TRACK_ONLY_TAB.splitlines()[1] + "\n"
    with (
        patch("muzilla.audio.replaygain.shutil.which", return_value="/usr/bin/rsgain"),
        patch("muzilla.audio.replaygain.subprocess.run", return_value=_completed(output)),
        pytest.raises(ReplayGainError, match="duplicate result"),
    ):
        compute_track_replaygain(Path("/music/a.flac"))


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
        patch(
            "muzilla.audio.replaygain.subprocess.run", return_value=_completed("File\tGain\tPeak\n")
        ),
        pytest.raises(ReplayGainError, match="no result"),
    ):
        compute_track_replaygain(Path("/music/a.flac"))


def test_compute_album_replaygain_empty_paths_returns_empty_dict() -> None:
    assert compute_album_replaygain([]) == {}
