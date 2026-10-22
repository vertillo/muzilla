"""ReplayGain 2.0 computation via `rsgain`.

Sync and CPU-bound, exactly like `audio/fingerprint.py`'s fpcalc wrapper:
this module only shells out and parses output, never writes tags itself.
`rsgain` supports an in-place tagging mode, but this module must not create
a second file-write path. It runs in `custom -O` scan-only mode, which emits
gain/peak values without touching files; callers stage the results as
ordinary changes and the reviewed applier performs the write.

Album gain needs every track of the album analyzed together in one
`rsgain` invocation (it's not decomposable per-file), so
`compute_album_replaygain` takes the whole track list at once.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path


class ReplayGainError(Exception):
    """Raised when rsgain is unavailable or fails on a file/album —
    callers should catch this per-item and continue, never let it abort
    a whole enrichment job; a corrupt file must never abort a bulk operation."""


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


def _run_rsgain(paths: list[Path], *, album: bool) -> dict[Path, TrackReplayGain]:
    if not paths:
        return {}
    binary = _rsgain_binary()
    aliases: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="muzilla-rsgain-") as directory:
        args = [binary, "custom", "-O", "-s", "s"]
        if album:
            args.append("-a")
        for index, path in enumerate(paths):
            alias = Path(directory) / f"{index}{path.suffix}"
            try:
                alias.symlink_to(path.absolute())
            except OSError as exc:
                raise ReplayGainError("unable to prepare rsgain input") from exc
            aliases[alias.name] = path
            args.append(str(alias))
        try:
            proc = subprocess.run(args, capture_output=True, text=True, check=True, timeout=300)
        except FileNotFoundError as exc:
            raise ReplayGainError("rsgain binary not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise ReplayGainError(f"rsgain timed out analyzing {len(paths)} file(s)") from exc
        except subprocess.CalledProcessError as exc:
            raise ReplayGainError(f"rsgain failed: {exc.stderr.strip()}") from exc
        return _parse_tab_output(proc.stdout, aliases)


def _parse_tab_output(stdout: str, aliases: dict[str, Path]) -> dict[Path, TrackReplayGain]:
    """Parse rsgain tab output and restore the original input paths."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return {}
    header = [field.strip() for field in lines[0].split("\t")]

    def col(*names: str) -> int | None:
        return next((header.index(name) for name in names if name in header), None)

    file_idx = col("Filename", "File")
    gain_idx = col("Gain (dB)", "Gain")
    peak_idx = col("Peak")
    album_gain_idx = col("Album Gain (dB)", "Album Gain")
    album_peak_idx = col("Album Peak")
    if file_idx is None or gain_idx is None or peak_idx is None:
        return {}

    original_paths = {str(path): path for path in aliases.values()}
    results: dict[Path, TrackReplayGain] = {}
    album_result: tuple[float, float] | None = None
    for line in lines[1:]:
        fields = [field.strip() for field in line.split("\t")]
        if max(file_idx, gain_idx, peak_idx) >= len(fields) or not fields[file_idx]:
            continue
        if fields[file_idx] == "Album":
            try:
                value = (_parse_db(fields[gain_idx]), float(fields[peak_idx]))
            except ValueError:
                continue
            if album_result is not None:
                raise ReplayGainError("rsgain produced duplicate album result")
            album_result = value
            continue

        output_name = fields[file_idx]
        path = aliases.get(output_name) or aliases.get(Path(output_name).name)
        if path is None:
            path = original_paths.get(output_name)
        if path is None:
            continue
        if path in results:
            raise ReplayGainError(f"rsgain produced duplicate result for {path}")
        try:
            track_gain = _parse_db(fields[gain_idx])
            track_peak = float(fields[peak_idx])
            album_gain = (
                _parse_db(fields[album_gain_idx])
                if album_gain_idx is not None
                and album_gain_idx < len(fields)
                and fields[album_gain_idx]
                else None
            )
            album_peak = (
                _try_float(fields[album_peak_idx])
                if album_peak_idx is not None and album_peak_idx < len(fields)
                else None
            )
        except (IndexError, ValueError):
            continue
        results[path] = TrackReplayGain(
            path=path,
            track_gain_db=track_gain,
            track_peak=track_peak,
            album_gain_db=album_gain,
            album_peak=album_peak,
        )

    if album_result is not None:
        for path, result in results.items():
            results[path] = replace(
                result,
                album_gain_db=(
                    result.album_gain_db if result.album_gain_db is not None else album_result[0]
                ),
                album_peak=(
                    result.album_peak if result.album_peak is not None else album_result[1]
                ),
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
    results = _run_rsgain([path], album=False)
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
    return _run_rsgain(paths, album=True)
