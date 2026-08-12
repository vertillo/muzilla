"""Pre-Phase-8 resource baseline (docs/product-spec.md, step 0.2).

Extends the §11g performance harness (`gen_perf_library.py` generates
the scratch data; this script measures RSS/connections around a real
`muzilla serve` process rather than in-process, since RSS is a
whole-process OS number that an in-process pytest run can't observe
correctly). Not shipped in the wheel — a dev-only tool.

Measures, against a scratch config/data dir, entirely isolated from any
real library or DB:

1. Idle RSS of `muzilla serve` — startup complete, worker pool running,
   no jobs.
2. Peak RSS during a scan of N synthetic tracks (default 10k, reusing
   `gen_perf_library.py`'s generator).
3. Actual SQLite connection count under that load (`PRAGMA
   database_list` doesn't expose this; counted via `lsof` on the DB
   file, which is what actually holds an OS-level file descriptor open
   per pooled connection).
4. Peak RSS of a single `POST /api/auth/login`.

RSS is read via `ps -o rss=`, not `psutil` (not a project dependency;
`ps` is dependency-free and this is a throwaway measurement script, not
shipped code).

Run: `python scripts/measure_resource_baseline.py --count 10000`
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).parent.parent


def _rss_kb(pid: int) -> int | None:
    """Current RSS in KB for `pid`, or None if the process has exited."""
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError:
        return None
    text = out.stdout.strip()
    return int(text) if text else None


def _open_fd_count(pid: int, db_path: Path) -> int:
    """Number of distinct numbered file descriptors the server process
    holds open on the DB file — a proxy for the live SQLite connection
    count, since SQLAlchemy's pool doesn't expose a live count via any
    public API and PRAGMA database_list only lists attached databases
    per-connection, not pool occupancy.

    `lsof -t <path>` only returns matching PIDs (one line per process,
    never per-FD), so it cannot distinguish multiple pooled connections
    within one process — confirmed empirically: it stayed at "1" under
    10 concurrent threads. `lsof -p <pid>` and counting numbered FD
    entries (`10u`, `11u`, ... — excluding the `txt` entry, which is the
    process's own mmap'd page of the file, not a connection) does track
    pool growth under load."""
    out = subprocess.run(["lsof", "-p", str(pid)], capture_output=True, text=True)
    db_path_str = str(db_path)
    count = 0
    for line in out.stdout.splitlines():
        if db_path_str not in line:
            continue
        fd_field = line.split()[3]
        if fd_field[:-1].isdigit() and fd_field[-1] in ("u", "r", "w"):
            count += 1
    return count


class RssSampler:
    """Polls RSS for a PID on a background thread and tracks the peak."""

    def __init__(self, pid: int, interval_s: float = 0.2) -> None:
        self._pid = pid
        self._interval_s = interval_s
        self._peak_kb = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            rss = _rss_kb(self._pid)
            if rss is not None:
                self._peak_kb = max(self._peak_kb, rss)
            time.sleep(self._interval_s)

    def __enter__(self) -> RssSampler:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    @property
    def peak_kb(self) -> int:
        return self._peak_kb


def _wait_for_health(base_url: str, timeout_s: float = 30) -> None:
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/health", timeout=2)
            if resp.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last_exc = exc
        time.sleep(0.3)
    raise RuntimeError(f"server never became healthy: {last_exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10_000, help="Synthetic track count.")
    parser.add_argument("--port", type=int, default=18461, help="Scratch server port.")
    args = parser.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="muzilla-baseline-"))
    library_dir = scratch / "library"
    data_dir = scratch / "data"
    library_dir.mkdir()
    data_dir.mkdir()
    base_url = f"http://127.0.0.1:{args.port}"
    password = "baseline-measurement-password"

    print(f"Scratch dir: {scratch}", file=sys.stderr)
    print(f"Generating {args.count} synthetic tracks...", file=sys.stderr)
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "gen_perf_library.py"),
            "--count",
            str(args.count),
            "--out",
            str(library_dir),
        ],
        check=True,
    )

    env = {
        **os.environ,
        "MUZILLA_STORAGE__LIBRARY_ROOT": str(library_dir),
        "MUZILLA_STORAGE__DATA_DIR": str(data_dir),
        "MUZILLA_STORAGE__DB_PATH": str(data_dir / "muzilla.db"),
        "MUZILLA_STORAGE__CACHE_DIR": str(data_dir / "cache"),
        "MUZILLA_STORAGE__BLOB_DIR": str(data_dir / "blobs"),
        "MUZILLA_AUTH__ENABLED": "true",
        "MUZILLA_AUTH__PASSWORD": password,
        "MUZILLA_AUTH__SESSION_SECRET": "baseline-measurement-session-secret",
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "muzilla.cli.main", "serve", "--port", str(args.port)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    results: dict[str, str] = {}
    client = httpx.Client(base_url=base_url, timeout=10)
    try:
        _wait_for_health(base_url)
        time.sleep(2)  # let the worker pool settle after the health check flips green
        idle_rss_kb = _rss_kb(proc.pid)
        assert idle_rss_kb is not None
        results["idle_rss_mb"] = f"{idle_rss_kb / 1024:.1f}"
        print(f"Idle RSS: {idle_rss_kb / 1024:.1f} MiB", file=sys.stderr)

        login_resp = client.post("/api/auth/login", json={"password": password})
        login_resp.raise_for_status()

        db_path = data_dir / "muzilla.db"
        with RssSampler(proc.pid) as sampler:
            resp = client.post("/api/scan", json={"root": str(library_dir)})
            resp.raise_for_status()
            job_id = resp.json()["job_id"]
            max_fds = 0
            while True:
                max_fds = max(max_fds, _open_fd_count(proc.pid, db_path))
                job = client.get(f"/api/jobs/{job_id}").json()
                if job["state"] in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(0.5)
            if job["state"] != "succeeded":
                raise RuntimeError(f"scan job did not succeed: {job}")
        results["scan_peak_rss_mb"] = f"{sampler.peak_kb / 1024:.1f}"
        results["sqlite_connections"] = str(max_fds)
        print(f"Scan peak RSS: {sampler.peak_kb / 1024:.1f} MiB", file=sys.stderr)
        print(f"SQLite connections (peak open FDs on db file): {max_fds}", file=sys.stderr)

        client.post("/api/auth/logout")
        with RssSampler(proc.pid) as sampler:
            resp = client.post("/api/auth/login", json={"password": password})
            resp.raise_for_status()
        results["login_peak_rss_mb"] = f"{sampler.peak_kb / 1024:.1f}"
        print(f"Login peak RSS: {sampler.peak_kb / 1024:.1f} MiB", file=sys.stderr)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        if proc.stdout is not None:
            server_output = proc.stdout.read()
            if proc.returncode not in (0, None, -15):
                print(server_output, file=sys.stderr)
        shutil.rmtree(scratch, ignore_errors=True)

    print("\n--- Results ---")
    for key, value in results.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
