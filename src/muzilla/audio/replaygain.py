"""ReplayGain 2.0 computation via `rsgain` (docs/PLAN.md §6, Tech Stack —
"ReplayGain via rsgain only").

Sync and CPU-bound, exactly like `audio/fingerprint.py`'s fpcalc wrapper:
this module only shells out and parses output, never writes tags itself.
`rsgain` supports an in-place tagging mode, but that would be a second,
untracked write path outside `changes/applier.py` — violating "nothing
touches disk until a ChangeSet is applied" (CLAUDE.md). So this always
runs in `custom -O tab` mode, which computes gain/peak values and prints
them without touching the files; callers stage the results as ordinary
`set` Changes on `rg_track_gain`/`rg_track_peak`/`r128_track_gain`, and
`changes/applier.py` writes them through the normal tag-write path.

Album gain needs every track of the album analyzed together in one
`rsgain` invocation (it's not decomposable per-file), so
`compute_album_replaygain` takes the whole track list at once.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ReplayGainError(Exception):
    """Raised when rsgain is unavailable or fails on a file/album —
    callers should catch this per-item and continue, never let it abort
    a whole enrichment job (docs/PLAN.md: a corrupt file must never
    abort a bulk operation)."""


@dataclass(frozen=True, slots=True)
class TrackReplayGain:
    path: Path
    track_gain_db: float
    track_peak: float
    album_gain_db: float | None = None
    album_peak: float | None = None


def _rsgain_binary() -> str:
    binary = shutil.which("rsgain")
    if binary is None:
        raise ReplayGainError("rsgain binary not found on PATH")
    return binary


def probe_replaygain_runtime(*, timeout_seconds: float = 5.0) -> tuple[bool, str]:
    """Start ``rsgain`` with a bounded, side-effect-free command.

    Checking only ``PATH`` misses broken dynamic-library linkage.  Details
    remain stable and sanitized because this result is exposed by public
    readiness endpoints.
    """
    try:
        binary = _rsgain_binary()
    except ReplayGainError:
        return False, "rsgain executable not found"

    try:
        result = subprocess.run(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, "rsgain capability probe timed out"
    except OSError:
        return False, "rsgain executable could not start"
    if result.returncode != 0:
        return False, "rsgain executable could not start"
    return True, "operational"


def _run_rsgain(paths: list[Path], *, album: bool) -> str:
    if not paths:
        return ""
    binary = _rsgain_binary()
    args = [binary, "custom", "-O", "tab"]
    if album:
        args.append("-a")
    args.extend(str(p) for p in paths)
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=True, timeout=300)
    except FileNotFoundError as exc:
        raise ReplayGainError("rsgain binary not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise ReplayGainError(f"rsgain timed out analyzing {len(paths)} file(s)") from exc
    except subprocess.CalledProcessError as exc:
        raise ReplayGainError(f"rsgain failed: {exc.stderr.strip()}") from exc
    return proc.stdout


def _parse_tab_output(stdout: str) -> dict[Path, TrackReplayGain]:
    """Parses `rsgain custom -O tab` output.

    Format is tab-separated with a header row:
    `File\tLoudness\tGain\tPeak\tPeak dB\tClipping\tClip-adjusted\tAlbum Loudness\tAlbum Gain\tAlbum Peak`
    — album columns are present (but blank per-row) only when `-a` was
    passed. Column count is detected from the header rather than assumed,
    since a non-album run has fewer columns.
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return {}
    header = lines[0].split("\t")

    def col(name: str) -> int | None:
        try:
            return header.index(name)
        except ValueError:
            return None

    gain_idx = col("Gain")
    peak_idx = col("Peak")
    album_gain_idx = col("Album Gain")
    album_peak_idx = col("Album Peak")

    results: dict[Path, TrackReplayGain] = {}
    for line in lines[1:]:
        fields = line.split("\t")
        if not fields or not fields[0] or gain_idx is None or peak_idx is None:
            continue
        path = Path(fields[0])
        try:
            track_gain = _parse_db(fields[gain_idx])
            track_peak = float(fields[peak_idx])
        except (IndexError, ValueError):
            continue
        album_gain = (
            _parse_db(fields[album_gain_idx])
            if album_gain_idx is not None and album_gain_idx < len(fields)
            else None
        )
        album_peak = (
            _try_float(fields[album_peak_idx])
            if album_peak_idx is not None and album_peak_idx < len(fields)
            else None
        )
        results[path] = TrackReplayGain(
            path=path,
            track_gain_db=track_gain,
            track_peak=track_peak,
            album_gain_db=album_gain,
            album_peak=album_peak,
        )
    return results


def _parse_db(raw: str) -> float:
    return float(raw.strip().removesuffix("dB").strip())


def _try_float(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def compute_track_replaygain(path: Path) -> TrackReplayGain:
    """Track-only ReplayGain for a singleton (no album context)."""
    stdout = _run_rsgain([path], album=False)
    results = _parse_tab_output(stdout)
    result = results.get(path)
    if result is None:
        raise ReplayGainError(f"rsgain produced no result for {path}")
    return result


def compute_album_replaygain(paths: list[Path]) -> dict[Path, TrackReplayGain]:
    """Track + album ReplayGain for a whole album's files, analyzed
    together in one `rsgain` invocation — album gain isn't decomposable
    per-file. Returns a dict keyed by the input paths; a file missing
    from the result (corrupt/unreadable) is simply absent, never raises
    for the whole batch."""
    stdout = _run_rsgain(paths, album=True)
    return _parse_tab_output(stdout)
