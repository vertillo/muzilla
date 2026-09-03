"""AcoustID fingerprinting via `fpcalc`/Chromaprint.

Sync, deliberately: fpcalc is a CPU-bound subprocess, not I/O — "async
only at the edges" means this stays a plain function callers run in a
bounded thread/process pool (`min(4, cpu_count)` per the scan pipeline
spec), not something this module manages itself. Kept in `audio/`
rather than `providers/` because it never touches the network — the
network half (submitting a fingerprint to AcoustID for lookup) is
`providers/acoustid.py`, which takes this module's output as input.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import acoustid


def probe_fingerprint_runtime(*, timeout_seconds: float = 5.0) -> tuple[bool, str]:
    """Probe fpcalc availability via a bounded, side-effect-free command."""
    binary = shutil.which("fpcalc")
    if binary is None:
        return False, "fpcalc executable not found"
    try:
        result = subprocess.run(
            [binary, "-h"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, "fpcalc capability probe timed out"
    except OSError:
        return False, "fpcalc executable could not start"
    # fpcalc -h exits 0 or 1 depending on version; any start without "not found" counts as available
    if "not found" in (result.stderr or ""):
        return False, "fpcalc executable could not start"
    return True, "operational"


class FingerprintError(Exception):
    """Raised when fpcalc/Chromaprint fails on a file — callers should
    catch this per-file and continue, exactly like a tag probe error
    A corrupt file must never abort a bulk scan."""


@dataclass(frozen=True, slots=True)
class Fingerprint:
    duration_s: float
    fingerprint: str
    """Base64-ish Chromaprint fingerprint string, as returned by fpcalc
    — the exact opaque format AcoustID's API expects, never decoded or
    interpreted locally."""


def compute_fingerprint(path: Path) -> Fingerprint:
    """Fingerprint one audio file.

    Raises `FingerprintError` on any failure (missing fpcalc binary,
    unreadable/corrupt audio, unsupported codec) — never lets a
    chromaprint/subprocess exception escape as something callers
    would have to know acoustid's internals to catch.
    """
    try:
        duration, fingerprint = acoustid.fingerprint_file(str(path))
    except acoustid.FingerprintGenerationError as exc:
        raise FingerprintError(f"fingerprinting failed for {path}: {exc}") from exc
    except OSError as exc:
        raise FingerprintError(f"fpcalc unavailable or file unreadable for {path}: {exc}") from exc
    if fingerprint is None:
        raise FingerprintError(f"fingerprinting failed for {path}: no fingerprint returned")
    fp_str = fingerprint.decode("ascii") if isinstance(fingerprint, bytes) else str(fingerprint)
    return Fingerprint(duration_s=float(duration), fingerprint=fp_str)
