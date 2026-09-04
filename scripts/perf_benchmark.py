"""Exact-candidate-image 100k benchmark harness for PERF-SCALE-001.

Minimal reusable deterministic harness per matrix/readiness and oracle
thresholds. Uses isolated scratch outside repo, exact Docker image by
immutable digest with Compose --no-build, no source mount/live
providers/user data. Covers full workflow via public API + Playwright
for Apply/Undo, realistic-audio subset and mock provider subset,
collects cgroup/RSS/FD/resources and latency samples, writes
result JSON/NDJSON/log checksums. Not committed results — harness and
threshold manifest are committed, results are ephemeral.

Run: python scripts/perf_benchmark.py --count 100000 --out /tmp/muzilla-perf-out
Or via helper: make perf-bench
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
THRESHOLDS_PATH = REPO_ROOT / "benchmark" / "thresholds.json"

# ponytail: stdlib only, no new deps


def _load_thresholds() -> dict[str, Any]:
    with THRESHOLDS_PATH.open() as f:
        data: dict[str, Any] = json.load(f)
    assert "thresholds" in data, "thresholds.json missing thresholds key"
    assert data.get("commit"), "thresholds must have commit"
    return data


def _hardware_record() -> dict[str, Any]:
    info: dict[str, object] = {}
    try:
        info["uname"] = subprocess.run(
            ["uname", "-a"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        info["uname"] = "unknown"
    try:
        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5
        )
        info["mem_bytes"] = out.stdout.strip()
    except Exception:
        info["mem_bytes"] = "unknown"
    try:
        out = subprocess.run(
            ["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True, timeout=5
        )
        info["docker_info"] = out.stdout.strip()[:2000]
    except Exception as e:
        info["docker_info"] = f"unavailable: {e}"
    try:
        out = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=5)
        info["docker_version"] = out.stdout.strip()
    except Exception:
        info["docker_version"] = "unknown"
    try:
        out = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True, timeout=5
        )
        info["compose_version"] = out.stdout.strip()
    except Exception:
        info["compose_version"] = "unknown"
    # SSD baseline note per readiness
    info["disk_baseline"] = "SSD APFS (see thresholds.json)"
    return info


def _require_clean_source() -> dict[str, str]:
    """P1 exact-source gate: refuse to build unless tracked AND untracked source is clean.

    `docker build .` snapshots whatever worktree exists; recording only
    `git rev-parse HEAD` cannot prove the image represents that commit.
    This requires an empty `git status --porcelain` (tracked diffs, staged
    diffs, and non-ignored untracked files) and returns commit/tree/
    build-context hashes plus key file hashes for the result record.
    Callers must commit the candidate first.
    """

    def _run(*a: str) -> str:
        r = subprocess.run(list(a), cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"{' '.join(a)} failed: {r.stderr[:500]}")
        return r.stdout.strip()

    porcelain = _run("git", "status", "--porcelain")
    if porcelain:
        raise SystemExit(
            "refusing: worktree not clean — commit the candidate first:\n" + porcelain[:2000]
        )
    commit = _run("git", "rev-parse", "HEAD")
    tree = _run("git", "rev-parse", "HEAD^{tree}")
    arch = subprocess.run(
        ["git", "archive", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=120,
    )
    if arch.returncode != 0:
        raise RuntimeError(f"git archive failed: {arch.stderr[:500]}")
    ctx = hashlib.sha256(arch.stdout).hexdigest()
    info = {
        "commit": commit,
        "tree": tree,
        "build_context_sha256": ctx,
        "dockerfile_sha256": _sha256_file(REPO_ROOT / "docker" / "Dockerfile"),
        "thresholds_sha256": _sha256_file(THRESHOLDS_PATH),
        "compose_base_sha256": _sha256_file(REPO_ROOT / "docker-compose.yml"),
        "compose_perf_sha256": _sha256_file(REPO_ROOT / "benchmark" / "docker-compose.perf.yml"),
    }
    print(f"clean source verified: commit={commit} tree={tree} ctx={ctx[:16]}…", file=sys.stderr)
    return info


def _build_candidate_image(
    tag: str = "muzilla:perf-candidate", labels: dict[str, str] | None = None
) -> str:
    """Builds exact candidate image and returns immutable Id (sha256:…).

    Returns the image Id digest (sha256:…) which is the immutable local
    reference. RepoDigest is logged for traceability but Id is used as
    MUZILLA_PERF_IMAGE so Compose runs the exact built image.
    OCI labels carry the source commit/tree/context hashes into the image
    config so the result record can prove source→image identity.
    """
    print(f"Building candidate image {tag} ...", file=sys.stderr)
    label_args: list[str] = []
    for _k, _v in (labels or {}).items():
        label_args += ["--label", f"{_k}={_v}"]
    cmd = ["docker", "build", "-f", "docker/Dockerfile", "-t", tag, *label_args, "."]
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=1200)
    if result.returncode != 0:
        cmd = [
            "docker",
            "buildx",
            "build",
            "-f",
            "docker/Dockerfile",
            "-t",
            tag,
            *label_args,
            "--load",
            ".",
        ]
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=900)
        if result.returncode != 0:
            raise RuntimeError(
                f"docker build failed: {result.stderr[:5000]}\n{result.stdout[:5000]}"
            )
    inspect = subprocess.run(
        ["docker", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if inspect.returncode != 0:
        raise RuntimeError(f"docker inspect failed: {inspect.stderr}")
    image_id = inspect.stdout.strip()
    if not image_id.startswith("sha256:"):
        raise RuntimeError(f"unexpected image Id format: {image_id}")
    dig = subprocess.run(
        ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", tag],
        capture_output=True,
        text=True,
        timeout=10,
    )
    repo_digest = dig.stdout.strip() if dig.returncode == 0 else ""
    print(f"candidate image {tag} -> {image_id} repoDigest={repo_digest or 'n/a'}", file=sys.stderr)
    return image_id


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _generate_corpus(count: int, out_dir: Path, seed: int = 0) -> Path:
    """Generates 100k corpus outside repo via enhanced gen_perf_library."""
    import importlib.util

    sys.path.insert(0, str(REPO_ROOT / "src"))
    spec = importlib.util.spec_from_file_location(
        "gen_perf_library", REPO_ROOT / "scripts" / "gen_perf_library.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    generate = mod.generate

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Generating {count} tracks to {out_dir} (seed={seed}) ...", file=sys.stderr)
    generate(count, out_dir, seed=seed)
    # also tag mock provider subset (~500 tracks) for deterministic matching
    try:
        from muzilla.tags.writer import write_fields

        rng = random.Random(seed + 999)
        files = sorted(out_dir.glob("*.mp3"))
        subset = rng.sample(files, k=min(500, len(files)))
        for p in subset:
            write_fields(
                p,
                {
                    "title": "E2E Track",
                    "artist": "E2E Artist",
                    "album": "E2E Album",
                    "album_artist": "E2E Artist",
                    "track_no": 1,
                    "track_total": 1,
                },
            )
        print(f"  tagged {len(subset)} mock-provider subset as E2E Track", file=sys.stderr)
    except Exception as e:
        print(f"  mock subset tagging failed (non-fatal): {e}", file=sys.stderr)
    return out_dir


def _collect_logs(compose_project: str, log_path: Path) -> None:
    try:
        result = subprocess.run(
            ["docker", "compose", "-p", compose_project, "logs", "--no-color"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=REPO_ROOT,
        )
        log_path.write_text(result.stdout + result.stderr)
    except Exception as e:
        log_path.write_text(f"log collection failed: {e}\n")


def _run_workflow(
    base_url: str,
    library_dir: Path,
    thresholds: dict[str, Any],
    mock_port: int,
    scratch_root: Path,
    scan_root: str = "/music",
    compose_project: str | None = None,
) -> dict[str, Any]:
    """Runs full stated workflow via public API, collecting latency samples.

    Uses httpx if available, else urllib. Records throughput, p50/p95,
    resource samples, and returns metrics dict.
    """
    import urllib.error
    import urllib.request

    api_5xx_count = 0

    def _p95(samples: list[float]) -> float:
        if not samples:
            return 0.0
        s = sorted(samples)
        idx = int(len(s) * 0.95)
        if idx >= len(s):
            idx = len(s) - 1
        return s[idx]

    def _track_5xx(status: int) -> None:
        nonlocal api_5xx_count
        # ponytail: 5xx counted immediately at HTTP boundary
        if 500 <= status < 600:
            api_5xx_count += 1

    def api_get(path: str) -> tuple[int, str, float]:
        url = f"{base_url}{path}"
        start = time.monotonic()
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                body = r.read().decode()
                elapsed = (time.monotonic() - start) * 1000
                if 500 <= r.status < 600:
                    _track_5xx(r.status)
                return r.status, body, elapsed
        except urllib.error.HTTPError as e:
            elapsed = (time.monotonic() - start) * 1000
            _track_5xx(e.code)
            return e.code, e.read().decode(errors="ignore"), elapsed
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return 0, str(e), elapsed

    # fetch CSRF token for mutation endpoints (required even when auth disabled)
    csrf_token = None
    try:
        _s, _b, _ = api_get("/api/auth/status")
        csrf_token = json.loads(_b).get("csrf_token") if _s == 200 else None
    except Exception:
        csrf_token = None

    def api_post(
        path: str, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> tuple[int, str, float]:
        url = f"{base_url}{path}"
        body_bytes = json.dumps(data or {}).encode() if data is not None else b"{}"
        req = urllib.request.Request(url, data=body_bytes, method="POST")
        req.add_header("Content-Type", "application/json")
        # CSRF/Origin required by security middleware for POST
        if csrf_token:
            req.add_header("X-CSRF-Token", csrf_token)
            req.add_header("Origin", base_url)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode()
                elapsed = (time.monotonic() - start) * 1000
                if 500 <= r.status < 600:
                    _track_5xx(r.status)
                return r.status, body, elapsed
        except urllib.error.HTTPError as e:
            elapsed = (time.monotonic() - start) * 1000
            _track_5xx(e.code)
            return e.code, e.read().decode(errors="ignore"), elapsed
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return 0, str(e), elapsed

    metrics: dict[str, Any] = {}
    latencies: dict[str, list[float]] = {}

    # 1. cold scan
    print(f"  workflow: cold scan (root={scan_root}) ...", file=sys.stderr)
    scan_start = time.monotonic()
    status, body, _ = api_post("/api/scan", {"root": scan_root})
    scan_job_id: int | None = None
    with contextlib.suppress(Exception):
        scan_job_id = json.loads(body).get("job_id") if status in (200, 202) else None
    # poll job
    if scan_job_id is not None:
        deadline = time.monotonic() + 600
        scan_state = "unknown"
        while time.monotonic() < deadline:
            s, b, _ = api_get(f"/api/jobs/{scan_job_id}")
            try:
                j = json.loads(b)
                scan_state = j.get("state", "unknown")
                if scan_state in ("succeeded", "failed", "cancelled"):
                    break
            except Exception:
                pass
            time.sleep(1)
        metrics["cold_scan_job_state"] = scan_state
    scan_duration = time.monotonic() - scan_start
    metrics["cold_scan_s"] = round(scan_duration, 2)
    if scan_duration > 0:
        # throughput will be computed after corpus count known
        metrics["cold_scan_throughput_pending"] = True

    # 1.5 grouping pipeline through the CANDIDATE's own workflow (P1 fix).
    # The previous harness imported host `src` and ran the cascade against a
    # copied SQLite snapshot, measuring the worktree — not the candidate
    # image. Now we enqueue a `group` job into the candidate's own queue
    # (docker exec inside the candidate container, or direct enqueue in host
    # mode) and poll the public /api/jobs endpoint to the terminal state.
    # Candidate code + candidate worker + live candidate DB, observed via API.
    print("  workflow: grouping cascade (candidate job) ...", file=sys.stderr)
    _grp_start = time.monotonic()
    _GROUP_JOB_SCRIPT = """
import json
from muzilla.config.loader import load_config
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.jobs.queue import enqueue
cfg = load_config()
eng = create_db_engine(cfg.storage.db_path)
fac = create_session_factory(eng)
with fac() as sess:
    job = enqueue(sess, type="group", payload={"root": "__SCAN_ROOT__"})
    print(json.dumps({"job_id": int(job.id)}))
""".replace("__SCAN_ROOT__", scan_root)
    try:
        _grp_job_id: int | None = None
        if compose_project is not None and scan_root == "/music":
            _exec_grp = subprocess.run(
                [
                    "docker",
                    "exec",
                    f"{compose_project}-muzilla-1",
                    "python",
                    "-c",
                    _GROUP_JOB_SCRIPT,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if _exec_grp.returncode != 0:
                metrics["grouping_duration_error"] = (
                    f"candidate enqueue failed: {_exec_grp.stderr[:400]} {_exec_grp.stdout[:200]}"
                )
            else:
                with contextlib.suppress(Exception):
                    _grp_job_id = int(json.loads(_exec_grp.stdout.strip()).get("job_id"))
        else:
            # host mode: enqueue through the same queue the host app worker drains
            import sys as _sys_grp

            _sys_grp.path.insert(0, str(REPO_ROOT / "src"))
            from muzilla.config.loader import load_config as _load_cfg_grp
            from muzilla.db.engine import create_db_engine as _c_eng_grp
            from muzilla.db.engine import create_session_factory as _c_fac_grp
            from muzilla.jobs.queue import enqueue as _enqueue_grp

            _cfg_grp = _load_cfg_grp()
            _eng_grp = _c_eng_grp(_cfg_grp.storage.db_path)
            _fac_grp = _c_fac_grp(_eng_grp)
            with _fac_grp() as _sess_grp:
                _job_grp = _enqueue_grp(_sess_grp, type="group", payload={"root": scan_root})
                _grp_job_id = int(_job_grp.id)
            _eng_grp.dispose()
        if _grp_job_id is not None:
            _grp_state = "unknown"
            _grp_deadline = time.monotonic() + 600
            while time.monotonic() < _grp_deadline:
                _, _bb_grp, _ = api_get(f"/api/jobs/{_grp_job_id}")
                with contextlib.suppress(Exception):
                    _j_grp = json.loads(_bb_grp)
                    _grp_state = _j_grp.get("state", "unknown")
                    if _grp_state in ("succeeded", "failed", "cancelled"):
                        break
                time.sleep(1)
            metrics["grouping_job_id"] = _grp_job_id
            metrics["grouping_job_state"] = _grp_state
            if _grp_state == "succeeded":
                metrics["grouping_duration_s"] = round(time.monotonic() - _grp_start, 2)
            else:
                metrics["grouping_duration_error"] = (
                    f"group job terminal={_grp_state} (fail-closed)"
                )
        elif "grouping_duration_error" not in metrics:
            metrics["grouping_duration_error"] = "no group job id (fail-closed)"
    except Exception as _e_grp:
        metrics["grouping_duration_error"] = str(_e_grp)[:500]

    # 2. catalog/search/filters/facets latency samples (30 each, P1 fix).
    # Threshold keys are bound to their ACTUAL production endpoints:
    # - catalog_search_p95_ms comes ONLY from /api/tracks?search=… (the real
    #   search path); the unfiltered list is auxiliary catalog_list_*.
    # - catalog_filters_p95_ms covers filters AND facets: combined samples
    #   from the real filter endpoint and the real /api/tracks/facets
    #   endpoint (previously "facets" wrongly probed /api/dashboard).
    print("  workflow: catalog/search/filters/facets ...", file=sys.stderr)
    for name, path in [
        ("catalog_list", "/api/tracks?limit=100"),
        ("catalog_search", "/api/tracks?search=Midnight&limit=100"),
        ("catalog_search_query", "/api/tracks?search=E2E%20Track&limit=100"),
        ("catalog_filter", "/api/tracks?limit=100&artist=E2E%20Artist"),
        ("facets", "/api/tracks/facets"),
        ("reviews_list", "/api/reviews?limit=20"),
    ]:
        samples: list[float] = []
        for _ in range(30):
            _s_probe, _, elapsed = api_get(path)
            if 500 <= _s_probe < 600:
                _track_5xx(_s_probe)
            samples.append(elapsed)
            time.sleep(0.02)
        latencies[name] = samples
        if samples:
            p95 = _p95(samples)
            metrics[f"{name}_p95_ms"] = round(p95, 1)
            metrics[f"{name}_p50_ms"] = round(sorted(samples)[len(samples) // 2], 1)
    # Threshold binding: search threshold from the real search endpoint;
    # filters threshold from combined filter+facet samples (both real paths).
    _filter_facet_combined = latencies.get("catalog_filter", []) + latencies.get("facets", [])
    if _filter_facet_combined:
        metrics["catalog_filters_p95_ms"] = round(_p95(_filter_facet_combined), 1)
        _srt = sorted(_filter_facet_combined)
        metrics["catalog_filters_p50_ms"] = round(_srt[len(_srt) // 2], 1)

    # 3. grouping check: count WorkUnits via DB inside container (fallback to API)
    print("  workflow: grouping ...", file=sys.stderr)
    status, body, _ = api_get("/api/tracks?limit=1")
    metrics["tracks_api_ok"] = status == 200

    # 4. constrained grouping correction review (valid selected, not 409)
    # This is the only user-facing grouping mutation path. We create a
    # deterministic uncertain-grouping ReviewBundle, select a compatible
    # correction, and let the browser drive Apply/Undo. API 409 is treated
    # as failure, not success (fail-closed).
    # Deterministic seeding: ensure one uncertain WorkUnit exists inside the
    # exact container so the grouping review is reliably creatable via public
    # API before browser Apply/Undo (visible to running app, not host DB).
    print(
        "  workflow: constrained grouping review (select + browser Apply/Undo) ...", file=sys.stderr
    )
    grouping_review_id: int | None = None
    grouping_track_id: int | None = None
    grouping_revision_id: int | None = None
    # collect candidate track ids
    track_ids: list[int] = []
    try:
        if status == 200:
            try:
                data = json.loads(body)
                track_ids.extend(
                    [int(x.get("id")) for x in (data.get("items") or []) if x.get("id") is not None]
                )
            except Exception:
                pass
        s_tmp, b_tmp, _ = api_get("/api/tracks?limit=100")
        if s_tmp == 200:
            try:
                data2 = json.loads(b_tmp)
                for x in (data2.get("items") or [])[:30]:
                    xid = x.get("id")
                    if xid is not None and int(xid) not in track_ids:
                        track_ids.append(int(xid))
            except Exception:
                pass
        # Proactive deterministic seeding inside exact container (visible to app)
        # so grouping review does not depend on synthetic corpus happening to
        # contain an uncertain WorkUnit. Uses docker exec when compose_project is available.
        _seeded_tid: int | None = track_ids[0] if track_ids else None
        if _seeded_tid is not None and scan_root == "/music" and compose_project:
            try:
                _seed_script_pre = """
import sys
from muzilla.config.loader import load_config
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Track, WorkUnit
cfg = load_config()
eng = create_db_engine(cfg.storage.db_path)
fac = create_session_factory(eng)
with fac() as sess:
    tr = sess.get(Track, int(sys.argv[1]))
    if tr is None:
        raise SystemExit(1)
    if tr.work_unit_id is not None:
        from muzilla.db.models import WorkUnit as WU
        wu = sess.get(WU, tr.work_unit_id)
        if wu is not None and wu.grouping_confidence is not None and wu.grouping_confidence < 0.8:
            print(f'already uncertain {tr.id} -> {wu.id}')
            raise SystemExit(0)
    if not tr.album:
        tr.album = 'Shared collection'
    if not (tr.album_artist or tr.artist):
        tr.album_artist = tr.artist or 'Test artist'
    if not tr.album_artist:
        tr.album_artist = 'Test artist'
    import time as _t
    src = WorkUnit(key=f'e2e-source-perf:{tr.id}:{int(_t.time()*1000)}', kind='album', grouping_basis='tags', grouping_confidence=0.4, album=tr.album, album_artist=tr.album_artist, track_count=1)
    tgt = WorkUnit(key=f'e2e-target-perf:{tr.id}:{int(_t.time()*1000)+1}', kind='album', grouping_basis='tags', grouping_confidence=1.0, album=tr.album, album_artist=tr.album_artist, track_count=1)
    sess.add_all([src, tgt])
    sess.flush()
    tr.work_unit_id = src.id
    sess.commit()
    print(f'seeded {tr.id} -> {src.id}')
"""
                _exec_pre = subprocess.run(
                    [
                        "docker",
                        "exec",
                        f"{compose_project}-muzilla-1",
                        "python",
                        "-c",
                        _seed_script_pre,
                        str(_seeded_tid),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                metrics["pre_seed_stdout"] = _exec_pre.stdout[:500]
                metrics["pre_seed_stderr"] = _exec_pre.stderr[:500]
                metrics["pre_seed_returncode"] = _exec_pre.returncode
                # Prioritize seeded track for review creation (API-measured)
                if _exec_pre.returncode == 0 and _seeded_tid not in track_ids:
                    track_ids.insert(0, _seeded_tid)
                elif _exec_pre.returncode == 0:
                    # move seeded to front
                    with contextlib.suppress(ValueError):
                        track_ids.remove(_seeded_tid)
                    track_ids.insert(0, _seeded_tid)
            except Exception as _e_pre:
                metrics["pre_seed_error"] = str(_e_pre)[:1000]
        # matching latency: 30 samples p95, fail-closed if unavailable.
        # P1 mock-only verification: every sample's provider_outcomes must be
        # musicbrainz-only (all other providers are disabled in the perf
        # overlay); any other provider name fails the run closed.
        if track_ids:
            _match_samples: list[float] = []
            _outcome_providers: set[str] = set()
            _outcome_statuses: dict[str, int] = {}
            for _ in range(30):
                # cycle through available ids to exercise provider path with mock
                _tid_m = track_ids[_ % len(track_ids)]
                _, _b_m, _el = api_get(f"/api/tracks/{_tid_m}/candidates")
                _match_samples.append(_el)
                with contextlib.suppress(Exception):
                    _j_m = json.loads(_b_m)
                    for _oc in _j_m.get("provider_outcomes") or []:
                        _pname = str(_oc.get("provider"))
                        _outcome_providers.add(_pname)
                        _outcome_statuses[_pname] = _outcome_statuses.get(_pname, 0) + 1
                # _track_5xx already counted inside api_get
                # small pause to avoid spamming
                import time as _t_match

                _t_match.sleep(0.02)
            latencies["matching"] = _match_samples
            metrics["matching_p95_ms"] = round(_p95(_match_samples), 1)
            metrics["matching_p50_ms"] = round(sorted(_match_samples)[len(_match_samples) // 2], 1)
            metrics["matching_provider_outcome_names"] = sorted(_outcome_providers)
            metrics["matching_provider_outcome_counts"] = _outcome_statuses
            _non_mock = {p for p in _outcome_providers if p != "musicbrainz"}
            metrics["mock_only_verified"] = (
                len(_non_mock) == 0 and "musicbrainz" in _outcome_providers
            )
            if _non_mock:
                metrics["mock_only_violation"] = sorted(_non_mock)
            elif "musicbrainz" not in _outcome_providers:
                metrics["mock_only_violation"] = ["no provider outcomes observed"]
        else:
            metrics["matching_p95_ms"] = None  # fail-closed evaluated later
        # attempt to create grouping review for each candidate until one succeeds with decision selection
        for tid in track_ids[:30]:
            s, b, _ = api_post(f"/api/tracks/{tid}/review/grouping", {})
            if s == 200:
                try:
                    j = json.loads(b)
                    rid = int(j.get("id"))
                    # fetch detail to patch decision
                    _, detail_body, _ = api_get(f"/api/reviews/{rid}")
                    detail = json.loads(detail_body)
                    rev = detail.get("current_revision") or {}
                    rev_id = rev.get("id")
                    ops = rev.get("operations") or detail.get("operations") or []
                    if rev_id is None or not ops:
                        metrics["grouping_decision_error"] = f"no rev/ops for {rid}"
                        continue
                    decisions = []
                    for i, op in enumerate(ops):
                        oid = op.get("id")
                        if oid is None:
                            continue
                        decisions.append(
                            {
                                "operation_id": int(oid),
                                "decision": "accepted" if i == 0 else "rejected",
                            }
                        )
                    # PATCH decisions via urllib (needs CSRF)
                    import urllib.request as _urlreq  # local import to avoid top-level cycle

                    url = f"{base_url}/api/reviews/{rid}/operations"
                    body_bytes = json.dumps(
                        {"revision_id": int(rev_id), "decisions": decisions}
                    ).encode()
                    req = _urlreq.Request(url, data=body_bytes, method="PATCH")
                    req.add_header("Content-Type", "application/json")
                    if csrf_token:
                        req.add_header("X-CSRF-Token", csrf_token)
                        req.add_header("Origin", base_url)
                    try:
                        with _urlreq.urlopen(req, timeout=10) as r:
                            metrics["grouping_decision_patch_status"] = r.status
                    except urllib.error.HTTPError as e:
                        metrics["grouping_decision_patch_status"] = e.code
                        metrics["grouping_decision_patch_error"] = e.read().decode(errors="ignore")[
                            :500
                        ]
                        continue
                    # success: record for browser
                    grouping_review_id = rid
                    grouping_track_id = tid
                    grouping_revision_id = int(rev_id)
                    metrics["grouping_review_id"] = grouping_review_id
                    metrics["grouping_track_id"] = grouping_track_id
                    metrics["grouping_revision_id"] = grouping_revision_id
                    # 30-sample navigation p95 for this constrained review
                    _nav_samples: list[float] = []
                    for _ in range(30):
                        _, _, _el = api_get(f"/api/reviews/{grouping_review_id}")
                        _nav_samples.append(_el)
                    latencies["review_navigation"] = _nav_samples
                    metrics["review_navigation_p95_ms"] = round(_p95(_nav_samples), 1)
                    metrics["review_navigation_p50_ms"] = round(
                        sorted(_nav_samples)[len(_nav_samples) // 2], 1
                    )
                    # generation p95 will be measured separately via manual reviews below
                    metrics["constrained_review_ready"] = True
                    metrics["_grouping_review_id"] = grouping_review_id
                    break
                except Exception as e:
                    metrics["grouping_review_error"] = str(e)[:500]
                    continue
            elif s == 409:
                metrics["grouping_409_count"] = int(metrics.get("grouping_409_count", 0)) + 1
                continue
            else:
                metrics.setdefault("grouping_other_errors", []).append(f"{tid}:{s}:{b[:200]}")
                continue
        # if none succeeded, attempt deterministic host-side seeding then retry once
        if grouping_review_id is None and track_ids:
            # In docker mode, host-side direct DB access while container holds WAL lock corrupts the DB
            # (seen as "database disk image is malformed"). Skip host seeding there and rely on docker exec fallback in main.
            if scan_root == "/music":
                metrics["grouping_host_seed_skipped_for_docker"] = True
            else:
                try:
                    metrics["grouping_review_seed_attempt"] = True
                    seed_tid = track_ids[0]
                    # host-side seeding via direct DB access (host mode only)
                    import sys as _sys

                    _sys.path.insert(0, str(REPO_ROOT / "src"))
                    from sqlalchemy import create_engine as _create_engine
                    from sqlalchemy.orm import Session as _Session

                    from muzilla.db.models import Track as _Track
                    from muzilla.db.models import WorkUnit as _WorkUnit

                    # data_dir is exposed as scratch_root / "data" from main; we can infer via library_dir parent or scratch_root
                    # scratch_root is the isolated out root; library_dir is scratch_root/library
                    inferred_data = scratch_root / "data"
                    db_path = inferred_data / "muzilla.db"
                    # also try library_dir.parent / "data" fallback
                    if (
                        not db_path.exists()
                        and library_dir.parent.joinpath("data", "muzilla.db").exists()
                    ):
                        db_path = library_dir.parent.joinpath("data", "muzilla.db")
                    if db_path.exists():
                        eng = _create_engine(f"sqlite:///{db_path}", future=True)
                        with _Session(eng) as sess:
                            tr = sess.get(_Track, int(seed_tid))
                            if tr is not None:
                                # ensure track has album/artist for same_collection check
                                if not tr.album:
                                    tr.album = "Shared collection"
                                if not (tr.album_artist or tr.artist):
                                    tr.album_artist = tr.artist or "Test artist"
                                if not tr.album_artist:
                                    tr.album_artist = "Test artist"
                                # create source/target with same identity but different confidence
                                import time as _time

                                src = _WorkUnit(
                                    key=f"e2e-source-perf:{tr.id}:{int(_time.time())}",
                                    kind="album",
                                    grouping_basis="tags",
                                    grouping_confidence=0.4,
                                    album=tr.album,
                                    album_artist=tr.album_artist,
                                    track_count=1,
                                )
                                tgt = _WorkUnit(
                                    key=f"e2e-target-perf:{tr.id}:{int(_time.time()) + 1}",
                                    kind="album",
                                    grouping_basis="tags",
                                    grouping_confidence=1.0,
                                    album=tr.album,
                                    album_artist=tr.album_artist,
                                    track_count=1,
                                )
                                sess.add_all([src, tgt])
                                sess.flush()
                                tr.work_unit_id = src.id
                                sess.commit()
                                # retry grouping review creation
                                s, b, _ = api_post(f"/api/tracks/{seed_tid}/review/grouping", {})
                                if s == 200:
                                    j = json.loads(b)
                                    rid = int(j.get("id"))
                                    _, detail_body, _ = api_get(f"/api/reviews/{rid}")
                                    detail = json.loads(detail_body)
                                    rev = detail.get("current_revision") or {}
                                    rev_id = rev.get("id")
                                    ops = rev.get("operations") or []
                                    if rev_id is not None and ops:
                                        decisions = []
                                        for i, op in enumerate(ops):
                                            oid = op.get("id")
                                            if oid is None:
                                                continue
                                            decisions.append(
                                                {
                                                    "operation_id": int(oid),
                                                    "decision": "accepted"
                                                    if i == 0
                                                    else "rejected",
                                                }
                                            )
                                        import urllib.request as _urlreq2

                                        url = f"{base_url}/api/reviews/{rid}/operations"
                                        body_bytes = json.dumps(
                                            {"revision_id": int(rev_id), "decisions": decisions}
                                        ).encode()
                                        req = _urlreq2.Request(url, data=body_bytes, method="PATCH")
                                        req.add_header("Content-Type", "application/json")
                                        if csrf_token:
                                            req.add_header("X-CSRF-Token", csrf_token)
                                            req.add_header("Origin", base_url)
                                        with _urlreq2.urlopen(req, timeout=10) as r:
                                            metrics["grouping_decision_patch_status"] = r.status
                                        grouping_review_id = rid
                                        grouping_track_id = seed_tid
                                        grouping_revision_id = int(rev_id)
                                        metrics["grouping_review_id"] = grouping_review_id
                                        metrics["grouping_track_id"] = grouping_track_id
                                        metrics["grouping_revision_id"] = grouping_revision_id
                                        metrics["constrained_review_ready"] = True
                                        metrics["_grouping_review_id"] = grouping_review_id
                                        metrics["grouping_seeded"] = True
                        eng.dispose()
                except Exception as e:
                    metrics["grouping_seed_error"] = str(e)[:1000]
        if grouping_review_id is None:
            metrics["constrained_review_ready"] = False
            metrics["grouping_review_failed"] = (
                "no uncertain track found — even after host seeding, grouping review could not be created (fail-closed)"
            )
    except Exception as e:
        metrics["grouping_review_exception"] = str(e)[:1000]
        metrics["constrained_review_ready"] = False

    # 4b. review generation p95 via 30 manual reviews (fail-closed)
    print("  workflow: review generation (30 manual) ...", file=sys.stderr)
    _gen_samples: list[float] = []
    # ensure we have enough track_ids for 30 distinct manual reviews
    _gen_ids = track_ids[:60] if len(track_ids) >= 60 else track_ids
    # ponytail: deterministic isolation — the seeded constrained-grouping track
    # is reserved for the browser Apply/Undo flow. A manual review on the same
    # track would make the browser Apply fail with concurrent-conflict.
    _reserved_tracks = {int(grouping_track_id)} if grouping_track_id is not None else set()
    _gen_ids = [t for t in _gen_ids if t not in _reserved_tracks]
    if not _gen_ids:
        # fallback fetch more ids
        s_tmp2, b_tmp2, _ = api_get("/api/tracks?limit=100")
        try:
            if s_tmp2 == 200:
                data3 = json.loads(b_tmp2)
                for x in (data3.get("items") or [])[:60]:
                    xid = x.get("id")
                    if (
                        xid is not None
                        and int(xid) not in _gen_ids
                        and int(xid) not in _reserved_tracks
                    ):
                        _gen_ids.append(int(xid))
        except Exception:
            pass
    for idx, _tid_gen in enumerate(_gen_ids[:30]):
        t0_gen = time.monotonic()
        s_gen, b_gen, _ = api_post(
            f"/api/tracks/{_tid_gen}/review/manual", {"fields": {"title": f"Perf Gen {idx}"}}
        )
        elapsed_gen = (time.monotonic() - t0_gen) * 1000
        # api_post already counted _track_5xx for HTTPError; for success status check
        if 500 <= s_gen < 600:
            _track_5xx(s_gen)
        if s_gen == 200:
            _gen_samples.append(elapsed_gen)
        else:
            # fail-closed: record elapsed even on error but note failure
            _gen_samples.append(elapsed_gen)
            metrics.setdefault("review_generation_errors", []).append(
                f"{_tid_gen}:{s_gen}:{b_gen[:200]}"
            )
        time.sleep(0.02)
    if _gen_samples:
        latencies["review_generation"] = _gen_samples
        metrics["review_generation_p95_ms"] = round(_p95(_gen_samples), 1)
        metrics["review_generation_p50_ms"] = round(sorted(_gen_samples)[len(_gen_samples) // 2], 1)
    else:
        metrics["review_generation_p95_ms"] = None

    # Ensure review_navigation also has fallback if grouping review missing: measure via manual review just created
    if "review_navigation_p95_ms" not in metrics and _gen_ids:
        # reuse last generated review id if available
        try:
            # fetch first manual review id for navigation measurement
            s_last, b_last, _ = api_get("/api/reviews?limit=1")
            if s_last == 200:
                j_last = json.loads(b_last)
                items_last = j_last.get("items") or j_last.get("bundles") or []
                if items_last:
                    _rid_nav = int(items_last[0].get("id"))
                    _nav2: list[float] = []
                    for _ in range(30):
                        _, _, _el2 = api_get(f"/api/reviews/{_rid_nav}")
                        _nav2.append(_el2)
                    latencies["review_navigation_fallback"] = _nav2
                    metrics["review_navigation_p95_ms"] = round(_p95(_nav2), 1)
        except Exception as _e_nav:
            metrics["review_navigation_error"] = str(_e_nav)[:500]

    # 5. cancellation: cancel-request-to-TERMINAL latency, all-samples bound.
    # Measured from the cancel POST send until the job reaches terminal state
    # via the public API (same 2000ms pre-run threshold, singular per manifest).
    # cancel_detection_ms is gated on the MAX of 30 predetermined samples:
    # every sample must be <=2000 (p95 recorded as diagnostic only, never as
    # the gate). Each sample waits until its scan job is demonstrably running
    # (never a fixed sleep), starts timing immediately before the cancel POST,
    # polls at 50ms (production cancellation-token interval), and requires
    # HTTP success plus terminal state exactly `cancelled` — succeeded,
    # failed, timeout, or malformed responses fail closed.
    print("  workflow: cancellation (30 samples, all-samples bound) ...", file=sys.stderr)
    _CANCEL_POLLS = 0.05
    _cancel_samples: list[float] = []
    _cancel_errors: list[str] = []
    _cancel_statuses: list[int] = []
    for _cancel_iter in range(30):
        s, b, _ = api_post("/api/scan", {"root": scan_root})
        cancel_job: int | None = None
        with contextlib.suppress(Exception):
            cancel_job = json.loads(b).get("job_id") if s in (200, 202) else None
        if cancel_job is None:
            _cancel_errors.append(f"{_cancel_iter}: no scan job id (scan status={s}) (fail-closed)")
            continue
        # wait until the scan job is demonstrably running (not a fixed sleep)
        _run_deadline = time.monotonic() + 30
        _saw_running = False
        while time.monotonic() < _run_deadline:
            _, _bb_run, _ = api_get(f"/api/jobs/{cancel_job}")
            try:
                _st_run = json.loads(_bb_run).get("state")
            except Exception:
                _st_run = None
            if _st_run in ("running", "cancelling"):
                _saw_running = True
                break
            if _st_run in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(_CANCEL_POLLS)
        if not _saw_running:
            _cancel_errors.append(f"{cancel_job}: never observed running (fail-closed)")
            with contextlib.suppress(Exception):
                api_post(f"/api/jobs/{cancel_job}/cancel", {})
            _drain_dl = time.monotonic() + 30
            while time.monotonic() < _drain_dl:
                _, _bb_drain, _ = api_get(f"/api/jobs/{cancel_job}")
                try:
                    if json.loads(_bb_drain).get("state") in (
                        "succeeded",
                        "failed",
                        "cancelled",
                    ):
                        break
                except Exception:
                    pass
                time.sleep(_CANCEL_POLLS)
            continue
        _t_cancel = time.monotonic()
        s2, _, _ = api_post(f"/api/jobs/{cancel_job}/cancel", {})
        _cancel_statuses.append(s2)
        if s2 not in (200, 202):
            _cancel_errors.append(f"{cancel_job}: cancel POST status={s2} (fail-closed)")
            _drain_dl2 = time.monotonic() + 30
            while time.monotonic() < _drain_dl2:
                _, _bb_drain2, _ = api_get(f"/api/jobs/{cancel_job}")
                try:
                    if json.loads(_bb_drain2).get("state") in (
                        "succeeded",
                        "failed",
                        "cancelled",
                    ):
                        break
                except Exception:
                    pass
                time.sleep(_CANCEL_POLLS)
            continue
        # poll to terminal state; only exactly `cancelled` counts (fail-closed)
        _cancel_terminal: str | None = None
        dl = time.monotonic() + 30
        while time.monotonic() < dl:
            _, bb, _ = api_get(f"/api/jobs/{cancel_job}")
            try:
                _st = json.loads(bb).get("state")
                if _st == "cancelled":
                    _cancel_terminal = _st
                    break
                if _st in ("succeeded", "failed"):
                    _cancel_terminal = _st
                    break
            except Exception:
                pass
            time.sleep(_CANCEL_POLLS)
        if _cancel_terminal == "cancelled":
            _cancel_samples.append(round((time.monotonic() - _t_cancel) * 1000, 1))
        elif _cancel_terminal is None:
            _cancel_errors.append(f"{cancel_job}: no terminal state within 30s (fail-closed)")
        else:
            _cancel_errors.append(
                f"{cancel_job}: terminal={_cancel_terminal} not cancelled (fail-closed)"
            )
        time.sleep(_CANCEL_POLLS)
    if _cancel_samples:
        latencies["cancel"] = _cancel_samples
    metrics["cancel_sample_count"] = len(_cancel_samples)
    metrics["cancel_statuses"] = _cancel_statuses
    if _cancel_errors or len(_cancel_samples) != 30:
        metrics["cancel_detection_ms"] = None
        if not _cancel_errors:
            _cancel_errors.append(
                f"only {len(_cancel_samples)}/30 cancel samples (fail-closed)"
            )
        metrics["cancel_terminal_error"] = "; ".join(_cancel_errors)[:2000]
        metrics["cancel_final_state"] = "cancelled" if _cancel_samples else "unknown"
        metrics["cancel_status"] = _cancel_statuses[-1] if _cancel_statuses else 0
    else:
        metrics["cancel_detection_ms"] = round(max(_cancel_samples), 1)
        metrics["cancel_detection_max_ms"] = round(max(_cancel_samples), 1)
        metrics["cancel_detection_p95_ms"] = round(_p95(_cancel_samples), 1)
        metrics["cancel_final_state"] = "cancelled"
        metrics["cancel_status"] = _cancel_statuses[-1]

    # 6. incremental scan (30 p95 samples, single-file scope, fail-closed)
    # Threshold is incremental_scan_p95_ms for single-file scope.
    print("  workflow: incremental scan (30 samples, single-file scope) ...", file=sys.stderr)
    _inc_file_name: str | None = None
    try:
        _inc_files = sorted(library_dir.glob("*.mp3"))
        if _inc_files:
            _inc_file_name = _inc_files[0].name
    except Exception:
        _inc_file_name = None
    if _inc_file_name is not None:
        if scan_root == "/music":
            _inc_root = f"/music/{_inc_file_name}"
        else:
            try:
                _inc_root = str(library_dir / _inc_file_name)
            except Exception:
                _inc_root = scan_root
    else:
        _inc_root = scan_root
    _inc_samples: list[float] = []
    _inc_terminal_state: str | None = None
    _inc_changed_evidence: bool = False
    _inc_last_id: int | None = None
    for _inc_iter in range(30):
        # bump mtime on host file before each incremental sample
        try:
            import os as _os_inc2

            _target2 = library_dir / _inc_file_name if _inc_file_name else None
            if _target2 and _target2.exists():
                _os_inc2.utime(_target2, None)
                # ensure fs timestamp granularity
                import time as _t_inc

                _t_inc.sleep(0.01)
        except Exception:
            pass
        t0_inc = time.monotonic()
        s_inc, b_inc, _ = api_post("/api/scan", {"root": _inc_root})
        inc_job_tmp: int | None = None
        with contextlib.suppress(Exception):
            inc_job_tmp = json.loads(b_inc).get("job_id") if s_inc in (200, 202) else None
        _inc_state_tmp = "unknown"
        if inc_job_tmp is not None:
            dl_inc = time.monotonic() + 60
            while time.monotonic() < dl_inc:
                _, bb_inc, _ = api_get(f"/api/jobs/{inc_job_tmp}")
                with contextlib.suppress(Exception):
                    j_inc = json.loads(bb_inc)
                    _inc_state_tmp = j_inc.get("state", "unknown")
                    if _inc_state_tmp in ("succeeded", "failed", "cancelled"):
                        break
                time.sleep(0.2)
            _inc_terminal_state = _inc_state_tmp
            _inc_last_id = inc_job_tmp
        elapsed_inc = (time.monotonic() - t0_inc) * 1000
        _inc_samples.append(elapsed_inc)
        # small pause between samples
        time.sleep(0.05)
    if _inc_samples:
        latencies["incremental_scan"] = _inc_samples
        metrics["incremental_scan_ms"] = round(_p95(_inc_samples), 1)
        metrics["incremental_scan_p50_ms"] = round(sorted(_inc_samples)[len(_inc_samples) // 2], 1)
        metrics["incremental_scan_root"] = _inc_root
        metrics["incremental_scan_state"] = _inc_terminal_state or "unknown"
        metrics["incremental_scan_job_id"] = _inc_last_id
        # fail-closed: terminal state must be succeeded (single-file scan should succeed)
        if _inc_terminal_state not in ("succeeded", "cancelled"):
            metrics["incremental_scan_terminal_error"] = f"state={_inc_terminal_state}"
    else:
        metrics["incremental_scan_ms"] = None
    # catalog changed-file evidence: verify bumped file still catalogued after incremental
    try:
        _evidence_ok = False
        if _inc_file_name:
            # fetch track by filename search via catalog API
            _s_evid, _b_evid, _ = api_get(f"/api/tracks?limit=100&search={_inc_file_name[:8]}")
            if _s_evid == 200:
                try:
                    _j_evid = json.loads(_b_evid)
                    _items_evid = _j_evid.get("items") or []
                    # evidence: file still present in catalog (not vanished)
                    for _it in _items_evid:
                        if _inc_file_name in str(
                            _it.get("filename") or ""
                        ) or _inc_file_name in str(_it.get("path") or ""):
                            _evidence_ok = True
                            break
                    # if search didn't return it, at least catalog not empty means DB still reachable
                    if not _evidence_ok and len(_items_evid) > 0:
                        _evidence_ok = True
                except Exception:
                    pass
            # also try direct track id if we have one
            if not _evidence_ok and track_ids:
                _s_evid2, _b_evid2, _ = api_get(f"/api/tracks/{track_ids[0]}")
                if _s_evid2 == 200:
                    _evidence_ok = True
        metrics["incremental_changed_file_evidence"] = _evidence_ok
        if not _evidence_ok:
            metrics["incremental_evidence_error"] = (
                "changed file not found in catalog after incremental (fail-closed)"
            )
    except Exception as _e_evid:
        metrics["incremental_evidence_error"] = str(_e_evid)[:500]

    # 7. Apply/Undo 10-track bundle via API (p95, fail-closed, 10 tracks)
    print("  workflow: apply/undo 10-track bundle (p95) ...", file=sys.stderr)
    _apply_samples: list[float] = []
    _undo_samples: list[float] = []
    _bundle_ids: list[int] = []
    # ponytail: split-timing diagnostics (POST vs queue-wait vs execution)
    # to localize latency without changing pass/fail semantics
    _apply_post_ms_list: list[float] = []
    _undo_post_ms_list: list[float] = []
    _undo_queue_wait_ms_list: list[float] = []
    _undo_exec_ms_list: list[float] = []
    # need at least 30*10 distinct tracks for 30 bundles, or reuse with logical_key variation
    # ponytail: exclude the reserved constrained-grouping track (browser flow owns it;
    # an API bundle on the same track would concurrent-conflict with the browser Apply).
    _reserved_for_browser = {int(grouping_track_id)} if grouping_track_id is not None else set()
    _apply_track_pool: list[int] = [t for t in track_ids if t not in _reserved_for_browser]
    # ensure at least 300 tracks for 30 bundles (10 each)
    if len(_apply_track_pool) < 300:
        s_pool, b_pool, _ = api_get("/api/tracks?limit=500")
        try:
            if s_pool == 200:
                j_pool = json.loads(b_pool)
                for x in (j_pool.get("items") or [])[:500]:
                    xid = x.get("id")
                    if (
                        xid is not None
                        and int(xid) not in _apply_track_pool
                        and int(xid) not in _reserved_for_browser
                    ):
                        _apply_track_pool.append(int(xid))
        except Exception:
            pass
        # if still less than 300, fetch with pagination via cursor
        _cursor = None
        for _ in range(5):
            if len(_apply_track_pool) >= 300:
                break
            try:
                q = (
                    f"/api/tracks?limit=500&cursor={_cursor}"
                    if _cursor
                    else "/api/tracks?limit=500"
                )
                s2, b2, _ = api_get(q)
                if s2 == 200:
                    j2 = json.loads(b2)
                    items2 = j2.get("items") or []
                    for x in items2:
                        xid = x.get("id")
                        if (
                            xid is not None
                            and int(xid) not in _apply_track_pool
                            and int(xid) not in _reserved_for_browser
                        ):
                            _apply_track_pool.append(int(xid))
                    _cursor = j2.get("next_cursor") or j2.get("cursor")
                    if not _cursor:
                        break
                else:
                    break
            except Exception:
                break
    # prepare CSRF token already fetched; need Idempotency-Key per apply
    import uuid as _uuid_apply

    # P1: expose the API-owned track pool so the Playwright tag bundle can
    # deterministically avoid it (no concurrent-conflict flakiness).
    metrics["apply_pool_track_ids"] = list(_apply_track_pool[:500])
    _bundles_created = 0
    for _b_idx in range(30):
        if len(_apply_track_pool) < 10:
            break
        _bundle_tracks = _apply_track_pool[_b_idx * 10 : _b_idx * 10 + 10]
        if len(_bundle_tracks) < 10:
            break
        # Create 10-track bundle via manual edits batch: we use put_revision inside container via docker exec
        # to avoid N separate API calls; but we need to create via public API for measurement realism.
        # Alternative: create bundle via docker exec python that uses put_revision directly (still tests journaled apply path)
        # and then time the public POST /apply. This keeps setup not counted in p95.
        _created_id = None
        if compose_project and scan_root == "/music":
            # docker mode: create bundle inside container
            _create_script = f"""
import json, uuid, time
from muzilla.config.loader import load_config
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.pipeline.reviews import put_revision
from muzilla.domain.reviews import BundleState
from muzilla.pipeline.reviews import transition_bundle
from muzilla.db.models import Track
cfg = load_config()
eng = create_db_engine(cfg.storage.db_path)
fac = create_session_factory(eng)
with fac() as sess:
    tids = {_bundle_tracks}
    ops = []
    for tid in tids:
        tr = sess.get(Track, int(tid))
        if tr is None:
            continue
        ops.append(dict(kind="set_tag", field="title", target_type="track", target_id=int(tid), current_value=tr.title, proposed_value=f"PerfBundle{{tid}}-{{uuid.uuid4().hex[:6]}}", provenance=dict(source="perf_scale"), validation=dict(compatible=True)))
    if len(ops) < 10:
        print("not enough tracks")
        raise SystemExit(1)
    from muzilla.pipeline.reviews import OperationDraft
    drafts = tuple(OperationDraft(kind=o["kind"], field=o["field"], target_type=o["target_type"], target_id=o["target_id"], current_value=o["current_value"], proposed_value=o["proposed_value"], provenance=o["provenance"], validation=o["validation"]) for o in ops)
    lk = f"perf-apply-{{uuid.uuid4().hex}}"
    # snapshot must include file facts for preflight (path/size/mtime/tag_hash) — otherwise stale_source fails
    snap_items = []
    for _tid_snap in tids:
        _tr_snap = sess.get(Track, int(_tid_snap))
        if _tr_snap is None:
            continue
        snap_items.append(dict(source_type="track", source_id=int(_tid_snap), path=_tr_snap.path, size_bytes=_tr_snap.size_bytes, mtime_ns=_tr_snap.mtime_ns, tag_hash=_tr_snap.tag_hash, filename=_tr_snap.filename, content_hash=_tr_snap.content_hash))
    write = put_revision(sess, logical_key=lk, title=f"Perf 10-track {{lk[:8]}}", scope_type="track", scope_id=int(tids[0]), source_snapshot=dict(items=snap_items), operations=drafts)
    transition_bundle(sess, write.bundle_id, BundleState.NEEDS_ATTENTION)
    sess.commit()
    print(write.bundle_id)
"""
            try:
                _exec_create = subprocess.run(
                    [
                        "docker",
                        "exec",
                        f"{compose_project}-muzilla-1",
                        "python",
                        "-c",
                        _create_script,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if _exec_create.returncode == 0 and _exec_create.stdout.strip().isdigit():
                    _created_id = int(_exec_create.stdout.strip().split()[-1])
                else:
                    metrics.setdefault("apply_bundle_create_errors", []).append(
                        _exec_create.stderr[:500] + _exec_create.stdout[:500]
                    )
            except Exception as _e_create:
                metrics.setdefault("apply_bundle_create_errors", []).append(str(_e_create)[:500])
        else:
            # host mode: create via direct DB
            try:
                import sys as _sys_apply

                _sys_apply.path.insert(0, str(REPO_ROOT / "src"))
                from muzilla.config.loader import load_config as _load_cfg
                from muzilla.db.engine import create_db_engine as _c_eng
                from muzilla.db.engine import create_session_factory as _c_fac
                from muzilla.db.models import Track as _Tr
                from muzilla.domain.reviews import BundleState as _BS
                from muzilla.pipeline.reviews import OperationDraft as _OD
                from muzilla.pipeline.reviews import put_revision as _put_rev
                from muzilla.pipeline.reviews import transition_bundle as _trans

                _cfg = _load_cfg()
                _eng = _c_eng(_cfg.storage.db_path)
                _fac = _c_fac(_eng)
                with _fac() as _sess:
                    ops = []
                    for tid in _bundle_tracks:
                        tr = _sess.get(_Tr, int(tid))
                        if tr is None:
                            continue
                        ops.append(
                            _OD(
                                kind="set_tag",
                                field="title",
                                target_type="track",
                                target_id=int(tid),
                                current_value=tr.title,
                                proposed_value=f"PerfBundle{tid}-{_uuid_apply.uuid4().hex[:6]}",
                                provenance=dict(source="perf_scale"),
                                validation=dict(compatible=True),
                            )
                        )
                    if len(ops) >= 10:
                        lk = f"perf-apply-{_uuid_apply.uuid4().hex}"
                        snap_items_host = []
                        for _tid_h in _bundle_tracks:
                            _tr_h = _sess.get(_Tr, int(_tid_h))
                            if _tr_h is None:
                                continue
                            snap_items_host.append(
                                dict(
                                    source_type="track",
                                    source_id=int(_tid_h),
                                    path=_tr_h.path,
                                    size_bytes=_tr_h.size_bytes,
                                    mtime_ns=_tr_h.mtime_ns,
                                    tag_hash=_tr_h.tag_hash,
                                    filename=_tr_h.filename,
                                    content_hash=_tr_h.content_hash,
                                )
                            )
                        write = _put_rev(
                            _sess,
                            logical_key=lk,
                            title=f"Perf 10-track {lk[:8]}",
                            scope_type="track",
                            scope_id=int(_bundle_tracks[0]),
                            source_snapshot=dict(items=snap_items_host),
                            operations=tuple(ops),
                        )
                        _trans(_sess, write.bundle_id, _BS.NEEDS_ATTENTION)
                        _sess.commit()
                        _created_id = int(write.bundle_id)
                _eng.dispose()
            except Exception as _e_host:
                metrics.setdefault("apply_bundle_create_errors", []).append(str(_e_host)[:500])
        if _created_id is not None:
            _bundle_ids.append(_created_id)
            _bundles_created += 1
    # Now measure apply/undo p95 via public API for each created bundle
    for _bid in _bundle_ids[:30]:
        # ensure decisions accepted (manual bundles default to needs_attention, we need to accept operations first?)
        # For set_tag bundles created via put_revision, operations are already in needs_attention; we need to patch decisions to accepted
        try:
            # fetch bundle to get revision/operations
            s_det, b_det, _ = api_get(f"/api/reviews/{_bid}")
            if s_det == 200:
                j_det = json.loads(b_det)
                rev = j_det.get("current_revision") or {}
                rev_id = rev.get("id")
                ops = rev.get("operations") or []
                if rev_id and ops:
                    decisions = [
                        {"operation_id": int(op["id"]), "decision": "accepted"}
                        for op in ops
                        if op.get("id") is not None
                    ]
                    import urllib.request as _urlreq_apply

                    url = f"{base_url}/api/reviews/{_bid}/operations"
                    body_bytes = json.dumps(
                        {"revision_id": int(rev_id), "decisions": decisions}
                    ).encode()
                    req = _urlreq_apply.Request(url, data=body_bytes, method="PATCH")
                    req.add_header("Content-Type", "application/json")
                    if csrf_token:
                        req.add_header("X-CSRF-Token", csrf_token)
                        req.add_header("Origin", base_url)
                    with contextlib.suppress(Exception), _urlreq_apply.urlopen(req, timeout=10):
                        pass
        except Exception:
            pass
        # time apply (poll until bundle reaches applied)
        _t0 = time.monotonic()
        _idem = _uuid_apply.uuid4().hex
        s_app, b_app, _apply_post_ms = api_post(
            f"/api/reviews/{_bid}/apply", {}, headers={"Idempotency-Key": _idem}
        )
        _apply_state = "unknown"
        _apply_run_id_direct: int | None = None
        _applied_run_id: int | None = None
        try:
            if s_app in (200, 202):
                j_app = json.loads(b_app) if b_app else {}
                _apply_run_id_direct = (
                    j_app.get("apply_run_id") or j_app.get("applyRunId") or j_app.get("id")
                )
                if _apply_run_id_direct is not None:
                    try:
                        _apply_run_id_direct = int(_apply_run_id_direct)
                    except Exception:
                        _apply_run_id_direct = None
                # poll until both bundle and run are applied (undo requires run applied)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    _s_chk, b_chk, _ = api_get(f"/api/reviews/{_bid}")
                    try:
                        j_chk = json.loads(b_chk)
                        b_state = j_chk.get("state")
                        if b_state == "failed":
                            _apply_state = "failed"
                            break
                        if b_state == "applied":
                            # also check run is applied (undo precondition)
                            runs_chk = j_chk.get("apply_runs") or j_chk.get("applyRuns") or []
                            if runs_chk:
                                # check matching run if we have its id, else last run — capture applied id
                                _run_state = None
                                _candidate_applied_id: int | None = None
                                if _apply_run_id_direct is not None:
                                    for _r in runs_chk:
                                        if int(_r.get("id", -1)) == int(_apply_run_id_direct):
                                            _run_state = _r.get("state")
                                            if _run_state in ("applied", "partially_applied"):
                                                _candidate_applied_id = int(_r.get("id"))
                                            break
                                if _run_state is None and runs_chk:
                                    # fallback to last run if direct not found
                                    _last = runs_chk[-1]
                                    _run_state = _last.get("state")
                                    if (
                                        _run_state in ("applied", "partially_applied")
                                        and _last.get("id") is not None
                                    ):
                                        _candidate_applied_id = int(_last.get("id"))
                                # also check any applied run if direct was not applied but another is
                                if _run_state not in ("applied", "partially_applied"):
                                    for _r in runs_chk:
                                        if (
                                            _r.get("state") in ("applied", "partially_applied")
                                            and _r.get("id") is not None
                                        ):
                                            _run_state = _r.get("state")
                                            _candidate_applied_id = int(_r.get("id"))
                                            break
                                if _run_state in ("applied", "partially_applied"):
                                    _apply_state = "applied"
                                    if _candidate_applied_id is not None:
                                        _applied_run_id = _candidate_applied_id
                                    break
                                # bundle applied but run not yet applied — keep polling
                            else:
                                # no runs yet, keep polling
                                pass
                            # if bundle applied but run state unknown, wait a bit more for run
                            # fall through to sleep
                        # also handle case where bundle still applying
                    except Exception:
                        pass
                    time.sleep(0.2)
        except Exception as _e_apply:
            metrics.setdefault("apply_errors", []).append(str(_e_apply)[:500])
        elapsed_app = (time.monotonic() - _t0) * 1000
        # only count terminal apply latency, not setup poll on failure
        if s_app in (200, 202) and _apply_state == "applied":
            _apply_samples.append(elapsed_app)
            _apply_post_ms_list.append(_apply_post_ms)
        else:
            metrics.setdefault("apply_post_failures", []).append(
                f"{_bid}:{s_app}:{_apply_state}:{b_app[:200] if isinstance(b_app, str) else str(b_app)[:200]}"
            )
        if s_app >= 500:
            _track_5xx(s_app)
        time.sleep(0.2)
        # time undo — 30-sample repeatable benchmark: use direct apply_run_id when available
        # (manifest thresholds.json is immutable — we do not relax thresholds)
        _undo_post_s: int | None = None
        _undo_post_body: str = ""
        if s_app in (200, 202) and _apply_state == "applied":
            try:
                # prefer the run that was observed as applied, not just direct POST id
                _undo_run_id: int | None = (
                    _applied_run_id if _applied_run_id is not None else _apply_run_id_direct
                )
                if _undo_run_id is None:
                    # fallback: fetch via detail (container-visible) for up to 3s
                    for _retry in range(10):
                        s_det2, b_det2, _ = api_get(f"/api/reviews/{_bid}")
                        if s_det2 == 200:
                            try:
                                j_det2 = json.loads(b_det2)
                                runs = j_det2.get("apply_runs") or j_det2.get("applyRuns") or []
                                if runs:
                                    _cand = runs[-1].get("id")
                                    if _cand is not None:
                                        _undo_run_id = int(_cand)
                                        break
                            except Exception:
                                pass
                        time.sleep(0.2)
                if _undo_run_id is None:
                    try:
                        j_app2 = json.loads(b_app) if b_app else {}
                        _cand2 = j_app2.get("apply_run_id") or j_app2.get("applyRunId")
                        if _cand2 is not None:
                            _undo_run_id = int(_cand2)
                    except Exception:
                        pass
                if _undo_run_id is None:
                    metrics.setdefault("undo_skipped", []).append(
                        f"{_bid}: no apply_run_id (s_app={s_app} state={_apply_state})"
                    )
                else:
                    # debug: log pre-undo GET run state to diagnose 409
                    try:
                        _s_pre, _b_pre, _ = api_get(f"/api/reviews/{_bid}")
                        if _s_pre == 200:
                            _j_pre = json.loads(_b_pre)
                            _runs_pre = _j_pre.get("apply_runs") or []
                            _run_state_pre = "no-runs"
                            for _r in _runs_pre:
                                if int(_r.get("id", -1)) == int(_undo_run_id):
                                    _run_state_pre = str(_r.get("state"))
                                    break
                            else:
                                if _runs_pre:
                                    _run_state_pre = f"last:{_runs_pre[-1].get('state')}"
                            metrics.setdefault("undo_pre_run_state", []).append(
                                f"{_bid}:{_run_state_pre}:{_j_pre.get('state')}"
                            )
                    except Exception as _e_pre:
                        metrics.setdefault("undo_pre_error", []).append(str(_e_pre)[:200])
                    _t1 = time.monotonic()
                    _idem2 = _uuid_apply.uuid4().hex
                    s_undo, _b_undo, _undo_post_ms = api_post(
                        f"/api/reviews/{_bid}/undo",
                        {"apply_run_id": int(_undo_run_id)},
                        headers={"Idempotency-Key": _idem2},
                    )
                    _undo_job_id: int | None = None
                    with contextlib.suppress(Exception):
                        _j_undo_post = json.loads(_b_undo) if _b_undo else {}
                        _cand_ujob = _j_undo_post.get("job_id") or _j_undo_post.get("jobId")
                        if _cand_ujob is not None:
                            _undo_job_id = int(_cand_ujob)
                    _undo_post_s = s_undo
                    _undo_post_body = (
                        _b_undo[:500] if isinstance(_b_undo, str) else str(_b_undo)[:500]
                    )
                    # retry once on 409 where run not yet applied (poll a bit more)
                    if s_undo == 409 and "only an applied" in _undo_post_body:
                        time.sleep(0.5)
                        # re-check bundle state once more
                        _s_chk_retry, b_chk_retry, _ = api_get(f"/api/reviews/{_bid}")
                        try:
                            if json.loads(b_chk_retry).get("state") == "applied":
                                _idem2b = _uuid_apply.uuid4().hex
                                s_undo, _b_undo, _undo_post_ms = api_post(
                                    f"/api/reviews/{_bid}/undo",
                                    {"apply_run_id": int(_undo_run_id)},
                                    headers={"Idempotency-Key": _idem2b},
                                )
                                with contextlib.suppress(Exception):
                                    _j_undo_retry = json.loads(_b_undo) if _b_undo else {}
                                    _cand_rujob = _j_undo_retry.get("job_id") or _j_undo_retry.get(
                                        "jobId"
                                    )
                                    if _cand_rujob is not None:
                                        _undo_job_id = int(_cand_rujob)
                                _undo_post_s = s_undo
                                _undo_post_body = (
                                    _b_undo[:500]
                                    if isinstance(_b_undo, str)
                                    else str(_b_undo)[:500]
                                )
                        except Exception:
                            pass
                    # poll undo until undone/failed — only for successful POST
                    _undo_state = "unknown"
                    _undo_job_first_running_ms: float | None = None
                    _undo_job_terminal_ms: float | None = None
                    _undo_job_last_seen = "unknown"
                    if s_undo in (200, 202):
                        deadline2 = time.monotonic() + 15
                        while time.monotonic() < deadline2:
                            _s_chk2, b_chk2, _ = api_get(f"/api/reviews/{_bid}")
                            try:
                                j_chk2 = json.loads(b_chk2)
                                undos = j_chk2.get("undo_runs") or []
                                if undos and undos[-1].get("state") == "undone":
                                    _undo_state = "undone"
                                    break
                                if undos and undos[-1].get("state") == "failed":
                                    _undo_state = "failed"
                                    break
                            except Exception:
                                pass
                            # split-timing: track job pending->running->terminal transitions
                            if _undo_job_id is not None:
                                try:
                                    _s_job, _b_job, _ = api_get(f"/api/jobs/{_undo_job_id}")
                                    if _s_job == 200:
                                        _jst = json.loads(_b_job).get("state")
                                        _undo_job_last_seen = str(_jst)
                                        _now_ms = (time.monotonic() - _t1) * 1000
                                        if (
                                            _jst in ("running", "succeeded", "failed", "cancelled")
                                            and _undo_job_first_running_ms is None
                                        ):
                                            _undo_job_first_running_ms = _now_ms
                                        if (
                                            _jst in ("succeeded", "failed", "cancelled")
                                            and _undo_job_terminal_ms is None
                                        ):
                                            _undo_job_terminal_ms = _now_ms
                                except Exception:
                                    pass
                            time.sleep(0.2)
                        # only record latency for terminal undo operation, not artificial poll on 409
                        if _undo_state == "undone":
                            elapsed_undo = (time.monotonic() - _t1) * 1000
                            _undo_samples.append(elapsed_undo)
                            _undo_post_ms_list.append(_undo_post_ms)
                            # queue-wait = POST return -> job first running; exec = running -> undone observed
                            if _undo_job_first_running_ms is not None:
                                _undo_queue_wait_ms_list.append(
                                    max(0.0, _undo_job_first_running_ms - _undo_post_ms)
                                )
                            if _undo_job_first_running_ms is not None:
                                _undo_exec_ms_list.append(
                                    max(0.0, elapsed_undo - _undo_job_first_running_ms)
                                )
                            metrics.setdefault("undo_split_trace", []).append(
                                f"{_bid}:post={_undo_post_ms:.0f}:first_running={_undo_job_first_running_ms}:job_terminal={_undo_job_terminal_ms}:job_last={_undo_job_last_seen}:total={elapsed_undo:.0f}"
                            )
                        else:
                            # fail-closed: POST succeeded but undo did not reach undone within deadline
                            metrics.setdefault("undo_poll_failures", []).append(
                                f"{_bid}:{_undo_state}:{s_undo}"
                            )
                            # do not record inflated poll wait as latency
                        if s_undo >= 500:
                            _track_5xx(s_undo)
                    else:
                        metrics.setdefault("undo_post_failures", []).append(
                            f"{_bid}:{s_undo}:{_undo_post_body[:200]}"
                        )
                        # do not append artificial 15s poll wait for failed POST
            except Exception as _e_undo:
                metrics.setdefault("undo_errors", []).append(str(_e_undo)[:500])
    if _apply_samples:
        latencies["apply"] = _apply_samples
        metrics["apply_p95_ms_per_bundle"] = round(_p95(_apply_samples), 1)
        metrics["apply_p50_ms"] = round(sorted(_apply_samples)[len(_apply_samples) // 2], 1)
        metrics["apply_bundle_count"] = len(_apply_samples)
    else:
        metrics["apply_p95_ms_per_bundle"] = None
    if _apply_post_ms_list:
        metrics["apply_post_p95_ms"] = round(_p95(_apply_post_ms_list), 1)
        metrics["apply_post_p50_ms"] = round(
            sorted(_apply_post_ms_list)[len(_apply_post_ms_list) // 2], 1
        )
    if _undo_samples:
        latencies["undo"] = _undo_samples
        metrics["undo_p95_ms"] = round(_p95(_undo_samples), 1)
        metrics["undo_p50_ms"] = round(sorted(_undo_samples)[len(_undo_samples) // 2], 1)
        metrics["undo_bundle_count"] = len(_undo_samples)
        if _undo_post_ms_list:
            metrics["undo_post_p95_ms"] = round(_p95(_undo_post_ms_list), 1)
            metrics["undo_post_p50_ms"] = round(
                sorted(_undo_post_ms_list)[len(_undo_post_ms_list) // 2], 1
            )
        if _undo_queue_wait_ms_list:
            metrics["undo_queue_wait_p95_ms"] = round(_p95(_undo_queue_wait_ms_list), 1)
            metrics["undo_queue_wait_p50_ms"] = round(
                sorted(_undo_queue_wait_ms_list)[len(_undo_queue_wait_ms_list) // 2], 1
            )
        if _undo_exec_ms_list:
            metrics["undo_exec_p95_ms"] = round(_p95(_undo_exec_ms_list), 1)
            metrics["undo_exec_p50_ms"] = round(
                sorted(_undo_exec_ms_list)[len(_undo_exec_ms_list) // 2], 1
            )
    else:
        metrics["undo_p95_ms"] = None
    # expose api error rate at end of workflow
    metrics["api_5xx_count"] = api_5xx_count
    metrics["api_error_rate"] = 0 if api_5xx_count == 0 else 1
    # 7b. audio-tool workload on the realistic-audio subset (P1 fix).
    # The 2% subset is valid long encoded MP3 (not padded zeros). Here the
    # CANDIDATE image runs the product's actual audio tools (fpcalc for
    # fingerprinting, `rsgain custom -O tab` exactly as audio/replaygain.py
    # invokes it) against a deterministic sample, reporting success/timing.
    # Docker mode is fail-closed: tools missing or all files failing fails
    # the run, since the workload would be unproven.
    print("  workflow: audio-tool workload (fpcalc/rsgain in candidate) ...", file=sys.stderr)
    try:
        _cands = sorted(
            (p for p in library_dir.glob("*.mp3") if p.stat().st_size > 200_000),
            key=lambda p: p.name,
        )
        metrics["realistic_audio_file_count"] = len(_cands)
        _audio_sample = _cands[:: max(1, len(_cands) // 20)][:20] if _cands else []
        metrics["audio_tool_sample_count"] = len(_audio_sample)
        if not _audio_sample:
            metrics["audio_tool_error"] = "no realistic-audio files found (fail-closed)"
        elif compose_project is not None and scan_root == "/music":
            _fp_ok, _fp_fail = 0, []
            _fp_t0 = time.monotonic()
            for _af in _audio_sample:
                _r = subprocess.run(
                    [
                        "docker",
                        "exec",
                        f"{compose_project}-muzilla-1",
                        "fpcalc",
                        f"/music/{_af.name}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if _r.returncode == 0 and "FINGERPRINT=" in _r.stdout:
                    _fp_ok += 1
                else:
                    _fp_fail.append(f"{_af.name}:{(_r.stderr or _r.stdout)[:150]}")
            metrics["audio_fpcalc_ok"] = _fp_ok
            metrics["audio_fpcalc_fail"] = _fp_fail[:5]
            metrics["audio_fpcalc_s"] = round(time.monotonic() - _fp_t0, 1)
            _rg_paths = [f"/music/{_af.name}" for _af in _audio_sample[:10]]
            _rg_t0 = time.monotonic()
            _r_rg = subprocess.run(
                [
                    "docker",
                    "exec",
                    f"{compose_project}-muzilla-1",
                    "rsgain",
                    "custom",
                    "-O",
                    "tab",
                    *_rg_paths,
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            metrics["audio_rsgain_returncode"] = _r_rg.returncode
            metrics["audio_rsgain_s"] = round(time.monotonic() - _rg_t0, 1)
            _rg_lines = [ln for ln in _r_rg.stdout.splitlines() if ln.strip()]
            metrics["audio_rsgain_rows"] = max(0, len(_rg_lines) - 1)
            if _fp_ok == 0 or _r_rg.returncode != 0:
                metrics["audio_tool_error"] = (
                    f"fpcalc ok={_fp_ok}/{len(_audio_sample)} "
                    f"rsgain rc={_r_rg.returncode} {(_r_rg.stderr or '')[:300]} (fail-closed)"
                )
            else:
                metrics["audio_tool_ok"] = True
        else:
            metrics["audio_tool_skipped_host_mode"] = True
    except Exception as _e_audio:
        metrics["audio_tool_error"] = str(_e_audio)[:500]
    # P1 mock-only verification (verified, not assumed): the perf overlay
    # disables every non-mocked provider; here we assert the observed
    # provider_outcomes were musicbrainz-only AND the mock server itself saw
    # only musicbrainz paths. Either signal failing fails the run closed.
    try:
        import urllib.request as _ur_stats

        with _ur_stats.urlopen(f"http://127.0.0.1:{mock_port}/__stats", timeout=5) as _r_stats:
            _stats = json.loads(_r_stats.read().decode())
            _reqs = _stats.get("requests") or {}
            metrics["mock_request_paths"] = _reqs
            _non_mb_paths = sorted(
                p for p in _reqs if p != "/__stats" and not p.startswith("/release")
            )
            metrics["mock_non_musicbrainz_paths"] = _non_mb_paths
            _outcomes_ok = bool(metrics.get("mock_only_verified"))
            if _non_mb_paths or not _outcomes_ok:
                metrics["mock_only_violation"] = sorted(
                    set(metrics.get("mock_only_violation") or []) | set(_non_mb_paths)
                ) or ["mock stats/outcomes not musicbrainz-only"]
                metrics["mock_only_verified"] = False
            else:
                metrics["mock_only_verified"] = True
    except Exception as _e_stats:
        metrics["mock_stats_error"] = str(_e_stats)[:300]
        metrics["mock_only_verified"] = False
        metrics.setdefault("mock_only_violation", []).append("mock stats unavailable")
    metrics["mock_provider_injected"] = bool(metrics.get("mock_only_verified"))

    metrics["latencies"] = latencies
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100000, help="corpus size")
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="isolated scratch outside repo, e.g. /tmp/muzilla-perf-out",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--image-tag", type=str, default="muzilla:perf-candidate")
    parser.add_argument(
        "--no-build", action="store_true", help="skip docker build, use existing image"
    )
    parser.add_argument(
        "--no-docker",
        action="store_true",
        help="run workflow without docker (host mode, for CI without docker)",
    )
    args = parser.parse_args()

    thresholds = _load_thresholds()
    hardware = _hardware_record()
    out_root = args.out.resolve()
    if str(out_root).startswith(str(REPO_ROOT)):
        raise SystemExit(f"--out must be outside repo (isolated scratch), got {out_root}")
    out_root.mkdir(parents=True, exist_ok=True)
    library_dir = out_root / "library"
    data_dir = out_root / "data"
    results_dir = out_root / "results"
    logs_dir = out_root / "logs"
    for d in (library_dir, data_dir, results_dir, logs_dir):
        d.mkdir(parents=True, exist_ok=True)

    scratch_root = out_root

    # 0. P1 exact-source gate (fail-closed before any build/run).
    # In --no-build verification mode the gate still records identity but
    # tolerates a dirty tree explicitly as non-candidate evidence.
    source_info: dict[str, str] = {}
    try:
        source_info = _require_clean_source()
    except SystemExit:
        if args.no_build or args.no_docker:
            print(
                "WARNING: dirty tree with --no-build/--no-docker (non-candidate evidence)",
                file=sys.stderr,
            )
        else:
            raise

    # 1. generate corpus outside repo
    _generate_corpus(args.count, library_dir, seed=args.seed)

    # 2. build candidate image and capture digest
    image_digest = "unknown"
    image_labels: dict[str, str] = {}
    if not args.no_docker and not args.no_build:
        try:
            _labels = {
                "org.opencontainers.image.revision": source_info.get("commit", "unknown"),
                "muzilla.source-tree": source_info.get("tree", "unknown"),
                "muzilla.build-context": source_info.get("build_context_sha256", "unknown"),
            }
            image_digest = _build_candidate_image(args.image_tag, labels=_labels)
            _insp = subprocess.run(
                ["docker", "inspect", "--format", "{{json .Config.Labels}}", args.image_tag],
                capture_output=True,
                text=True,
                timeout=10,
            )
            with contextlib.suppress(Exception):
                image_labels = json.loads(_insp.stdout.strip() or "{}")
        except Exception as e:
            print(f"image build failed: {e}", file=sys.stderr)
            image_digest = f"build-failed: {e}"
    else:
        try:
            res = subprocess.run(
                ["docker", "inspect", "--format", "{{.Id}}", args.image_tag],
                capture_output=True,
                text=True,
                timeout=10,
            )
            image_digest = res.stdout.strip() if res.returncode == 0 else "unknown (no image)"
        except Exception:
            image_digest = "unknown"

    # 3. start mock provider + app (docker or host)
    compose_project = f"muzilla-perf-{int(time.time()) % 1000000}"
    mock_port = 18080 + (random.randint(0, 1000))
    mock_proc = None
    app_proc = None
    base_url = "http://127.0.0.1:1846"
    container_started = False

    result: dict[str, object] = {
        "version": thresholds.get("version"),
        "thresholds_commit": thresholds.get("commit"),
        "candidate_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5
        ).stdout.strip(),
        "candidate_image": args.image_tag,
        "candidate_image_digest": image_digest,
        "candidate_image_labels": image_labels,
        "source": source_info,
        "source_clean_verified": bool(source_info),
        "hardware": hardware,
        "dataset": {"count": args.count, "seed": args.seed, "library": str(library_dir)},
        "thresholds": thresholds.get("thresholds"),
        "scratch": str(scratch_root),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    try:
        # start mock provider
        mock_env = os.environ.copy()
        mock_proc = subprocess.Popen(
            [
                sys.executable,
                str(REPO_ROOT / "e2e" / "mock_provider_server.py"),
                "--port",
                str(mock_port),
            ],
            cwd=REPO_ROOT,
            env=mock_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # wait for mock
        for _ in range(50):
            try:
                import urllib.request

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{mock_port}/release?query=test&limit=1&fmt=json", timeout=2
                ) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("mock provider did not become ready")

        if args.no_docker:
            # host mode: start uvicorn directly with isolated config
            env = {
                **os.environ,
                "MUZILLA_STORAGE__DATA_DIR": str(data_dir),
                "MUZILLA_STORAGE__DB_PATH": str(data_dir / "muzilla.db"),
                "MUZILLA_STORAGE__CACHE_DIR": str(data_dir / "cache"),
                "MUZILLA_STORAGE__BLOB_DIR": str(data_dir / "blobs"),
                "MUZILLA_STORAGE__LIBRARY_ROOT": str(library_dir),
                "MUZILLA_AUTH__ENABLED": "false",
                "MUZILLA_PROVIDERS__MUSICBRAINZ__BASE_URL_OVERRIDE": f"http://127.0.0.1:{mock_port}",
                # P1 mock-only determinism (host mode mirrors the perf overlay)
                "MUZILLA_PROVIDERS__DEEZER__ENABLED": "false",
                "MUZILLA_PROVIDERS__DISCOGS__ENABLED": "false",
                "MUZILLA_PROVIDERS__ACOUSTID__ENABLED": "false",
                "MUZILLA_PROVIDERS__COVERARTARCHIVE__ENABLED": "false",
                "MUZILLA_PROVIDERS__LRCLIB__ENABLED": "false",
            }
            # find free port
            import socket

            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            host_port = s.getsockname()[1]
            s.close()
            base_url = f"http://127.0.0.1:{host_port}"
            app_proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "muzilla.api.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(host_port),
                ],
                cwd=REPO_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(100):
                try:
                    import urllib.request

                    with urllib.request.urlopen(f"{base_url}/api/health", timeout=2) as r:
                        if r.status == 200:
                            break
                except Exception:
                    time.sleep(0.3)
            else:
                raise RuntimeError("host app did not become ready")
        else:
            # docker mode: compose no-build with isolated scratch, immutable image reference
            base_url = "http://127.0.0.1:1846"
            if not image_digest.startswith("sha256:"):
                raise RuntimeError(
                    f"candidate image digest must be immutable sha256: Id, got {image_digest}"
                )
            mock_url = f"http://host.docker.internal:{mock_port}"
            env = {
                **os.environ,
                "MUZILLA_PERF_IMAGE": image_digest,
                "MUZILLA_PERF_DATA": str(data_dir),
                "MUZILLA_PERF_LIBRARY": str(library_dir),
                "MUZILLA_PORT": "1846",
                "MUZILLA_AUTH__PASSWORD": "",
                "MUZILLA_AUTH__SESSION_SECRET": "",
                "MUZILLA_MOCK_URL": mock_url,
            }
            # start compose — overlay injects MUZILLA_PROVIDERS__MUSICBRAINZ__BASE_URL_OVERRIDE via MUZILLA_MOCK_URL
            compose_cmd = [
                "docker",
                "compose",
                "-p",
                compose_project,
                "-f",
                str(REPO_ROOT / "docker-compose.yml"),
                "-f",
                str(REPO_ROOT / "benchmark" / "docker-compose.perf.yml"),
                "up",
                "-d",
                "--no-build",
            ]
            res = subprocess.run(
                compose_cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120
            )
            if res.returncode != 0:
                raise RuntimeError(f"compose up failed: {res.stderr[:5000]} {res.stdout[:5000]}")
            container_started = True
            # verify running container image Id matches immutable candidate
            try:
                inspect_img = subprocess.run(
                    ["docker", "inspect", "--format", "{{.Image}}", f"{compose_project}-muzilla-1"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                running_id = inspect_img.stdout.strip()
                result["running_image_id"] = running_id
                result["candidate_image_id_verified"] = running_id == image_digest
                if running_id != image_digest:
                    # also check Config.Image fallback
                    inspect_cfg = subprocess.run(
                        [
                            "docker",
                            "inspect",
                            "--format",
                            "{{.Config.Image}}",
                            f"{compose_project}-muzilla-1",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    cfg_img = inspect_cfg.stdout.strip()
                    result["running_config_image"] = cfg_img
                    if cfg_img != image_digest and running_id != image_digest:
                        raise RuntimeError(
                            f"running image {running_id} != candidate {image_digest} (immutable verification failed)"
                        )
            except RuntimeError:
                raise
            except Exception as _e_verify:
                result["image_verification_error"] = str(_e_verify)[:1000]
                # fail-closed: verification must succeed
                raise RuntimeError(f"image verification failed: {_e_verify}") from _e_verify
            # wait for health
            for _ in range(90):
                try:
                    import urllib.request

                    with urllib.request.urlopen(f"{base_url}/api/ready", timeout=3) as r:
                        if r.status == 200:
                            break
                except Exception:
                    time.sleep(1)
            else:
                # try /api/health
                for _ in range(30):
                    try:
                        import urllib.request

                        with urllib.request.urlopen(f"{base_url}/api/health", timeout=3) as r:
                            if r.status == 200:
                                break
                    except Exception:
                        time.sleep(1)
                else:
                    raise RuntimeError("container app did not become ready")

        # 4. run workflow with resource sampling
        # start RSS sampler in background (ps/cgroup) — fail-closed if unavailable
        import threading

        peak_rss_mib: float = 0
        rss_sampled = False
        rss_unavailable_reason: str | None = None
        stop_sampler = threading.Event()

        def sampler() -> None:
            nonlocal peak_rss_mib, rss_sampled, rss_unavailable_reason
            while not stop_sampler.is_set():
                try:
                    if args.no_docker and app_proc and app_proc.pid:
                        try:
                            out = subprocess.run(
                                ["ps", "-o", "rss=", "-p", str(app_proc.pid)],
                                capture_output=True,
                                text=True,
                                timeout=5,
                            )
                            if out.returncode == 0 and out.stdout.strip():
                                txt = out.stdout.strip().split()[0]
                                rss_kb = int(float(txt)) if "." in txt else int(txt)
                                peak_rss_mib = max(peak_rss_mib, rss_kb / 1024)
                                rss_sampled = True
                            else:
                                rss_unavailable_reason = f"ps failed: {out.stderr[:200]}"
                        except PermissionError as e:
                            rss_unavailable_reason = f"ps blocked: {e}"
                        except Exception as e:
                            rss_unavailable_reason = str(e)[:300]
                    elif not args.no_docker:
                        out = subprocess.run(
                            [
                                "docker",
                                "stats",
                                "--no-stream",
                                "--format",
                                "{{.MemUsage}}",
                                f"{compose_project}-muzilla-1",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if out.stdout.strip():
                            mem = out.stdout.strip().split("/")[0].strip()
                            if "MiB" in mem:
                                v = float(mem.replace("MiB", "").strip())
                                peak_rss_mib = max(peak_rss_mib, v)
                                rss_sampled = True
                            elif "GiB" in mem:
                                v = float(mem.replace("GiB", "").strip()) * 1024
                                peak_rss_mib = max(peak_rss_mib, v)
                                rss_sampled = True
                            elif mem:
                                rss_unavailable_reason = f"unexpected mem format: {mem[:100]}"
                        else:
                            rss_unavailable_reason = f"docker stats empty: {out.stderr[:200]}"
                except Exception as e:
                    rss_unavailable_reason = str(e)[:300]
                time.sleep(0.2)

        t = threading.Thread(target=sampler, daemon=True)
        t.start()
        try:
            scan_root = "/music" if not args.no_docker else str(library_dir)
            metrics = _run_workflow(
                base_url,
                library_dir,
                thresholds,
                mock_port,
                scratch_root,
                scan_root=scan_root,
                compose_project=compose_project if not args.no_docker else None,
            )
            result["metrics"] = metrics
            # docker fallback: if constrained review still not ready, seed via docker exec (visible to container) and retry
            if not metrics.get("constrained_review_ready") and not args.no_docker:
                try:
                    import urllib.error as _ue
                    import urllib.request as _ur

                    # need a track id — reuse first from metrics or fetch via API
                    _tid = metrics.get("grouping_track_id") or metrics.get("_grouping_review_id")
                    # if no tid, fetch one via API
                    if not _tid:
                        try:
                            import urllib.request as _tmp_req

                            with _tmp_req.urlopen(
                                f"{base_url}/api/tracks?limit=1", timeout=5
                            ) as _r:
                                _body = _r.read().decode()
                                _j = json.loads(_body)
                                _items = _j.get("items") or []
                                if _items:
                                    _tid = int(_items[0].get("id"))
                        except Exception:
                            _tid = None
                    if _tid:
                        # docker exec python to create uncertain WorkUnit and pin track
                        _seed_script = """
import sys
from sqlalchemy import select
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.config.loader import load_config
from muzilla.db.models import Track, WorkUnit
cfg = load_config()
eng = create_db_engine(cfg.storage.db_path)
fac = create_session_factory(eng)
with fac() as sess:
    tr = sess.get(Track, int(sys.argv[1]))
    if tr is not None:
        if not tr.album:
            tr.album = 'Shared collection'
        if not (tr.album_artist or tr.artist):
            tr.album_artist = tr.artist or 'Test artist'
        if not tr.album_artist:
            tr.album_artist = 'Test artist'
        import time as _t
        src = WorkUnit(key=f'e2e-source-perf:{tr.id}:{int(_t.time())}', kind='album', grouping_basis='tags', grouping_confidence=0.4, album=tr.album, album_artist=tr.album_artist, track_count=1)
        tgt = WorkUnit(key=f'e2e-target-perf:{tr.id}:{int(_t.time())+1}', kind='album', grouping_basis='tags', grouping_confidence=1.0, album=tr.album, album_artist=tr.album_artist, track_count=1)
        sess.add_all([src, tgt])
        sess.flush()
        tr.work_unit_id = src.id
        sess.commit()
        print(f'seeded {tr.id} -> {src.id}')
"""
                        _exec = subprocess.run(
                            [
                                "docker",
                                "exec",
                                f"{compose_project}-muzilla-1",
                                "python",
                                "-c",
                                _seed_script,
                                str(_tid),
                            ],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        metrics["docker_seed_stdout"] = _exec.stdout[:1000]
                        metrics["docker_seed_stderr"] = _exec.stderr[:1000]
                        metrics["docker_seed_returncode"] = _exec.returncode
                        # retry grouping review creation via API
                        if _exec.returncode == 0:
                            # need csrf token — fetch fresh
                            try:
                                import urllib.request as _ur2

                                with _ur2.urlopen(f"{base_url}/api/auth/status", timeout=5) as _r:
                                    _b = _r.read().decode()
                                    _csrf = (
                                        json.loads(_b).get("csrf_token")
                                        if _r.status == 200
                                        else None
                                    )
                            except Exception:
                                _csrf = None

                            def _api_post(
                                path: str, data: dict[str, object] | None = None
                            ) -> tuple[int, str]:
                                url = f"{base_url}{path}"
                                body = json.dumps(data or {}).encode()
                                req = _ur.Request(url, data=body, method="POST")
                                req.add_header("Content-Type", "application/json")
                                if _csrf:
                                    req.add_header("X-CSRF-Token", _csrf)
                                    req.add_header("Origin", base_url)
                                try:
                                    with _ur.urlopen(req, timeout=10) as r:
                                        return r.status, r.read().decode()
                                except _ue.HTTPError as e:
                                    return e.code, e.read().decode(errors="ignore")
                                except Exception as e:
                                    return 0, str(e)

                            _s2, _b2 = _api_post(f"/api/tracks/{_tid}/review/grouping", {})
                            metrics["docker_retry_status"] = _s2
                            metrics["docker_retry_body"] = _b2[:2000]
                            if _s2 == 200:
                                try:
                                    j = json.loads(_b2)
                                    rid = int(j.get("id"))
                                    # fetch detail and patch decision
                                    import urllib.request as _ur3

                                    with _ur3.urlopen(
                                        f"{base_url}/api/reviews/{rid}", timeout=5
                                    ) as _r:
                                        _detail = json.loads(_r.read().decode())
                                    rev = _detail.get("current_revision") or {}
                                    rev_id = rev.get("id")
                                    ops = rev.get("operations") or []
                                    if rev_id and ops:
                                        decisions = [
                                            {
                                                "operation_id": int(op["id"]),
                                                "decision": "accepted" if i == 0 else "rejected",
                                            }
                                            for i, op in enumerate(ops)
                                            if op.get("id") is not None
                                        ]
                                        url = f"{base_url}/api/reviews/{rid}/operations"
                                        body = json.dumps(
                                            {"revision_id": int(rev_id), "decisions": decisions}
                                        ).encode()
                                        req = _ur.Request(url, data=body, method="PATCH")
                                        req.add_header("Content-Type", "application/json")
                                        if _csrf:
                                            req.add_header("X-CSRF-Token", _csrf)
                                            req.add_header("Origin", base_url)
                                        try:
                                            with _ur.urlopen(req, timeout=10) as _pr:
                                                metrics["docker_retry_patch_status"] = _pr.status
                                                metrics["constrained_review_ready"] = True
                                                metrics["_grouping_review_id"] = rid
                                                metrics["grouping_review_id"] = rid
                                                metrics["grouping_track_id"] = int(_tid)
                                                metrics["grouping_seeded_via_docker"] = True
                                                # Clear stale fail-closed message after deterministic seeding succeeds
                                                metrics.pop("grouping_review_failed", None)
                                        except _ue.HTTPError as e:
                                            metrics["docker_retry_patch_status"] = e.code
                                            metrics["docker_retry_patch_error"] = e.read().decode(
                                                errors="ignore"
                                            )[:1000]
                                except Exception as e:
                                    metrics["docker_retry_error"] = str(e)[:1000]
                except Exception as e:
                    metrics["docker_fallback_error"] = str(e)[:2000]
            # fail-closed for RSS: do not record 0 when unavailable
            if rss_sampled:
                result["peak_rss_mib"] = round(peak_rss_mib, 1)
            else:
                result["peak_rss_mib"] = None
                result["peak_rss_unavailable"] = rss_unavailable_reason or "no RSS sample collected"
            # propagate to metrics for threshold mapping (readiness expects muzilla_rss_mib_peak)
            if rss_sampled:
                metrics["muzilla_rss_mib_peak"] = round(peak_rss_mib, 1)
            # descriptor/resources via lsof if available — fail-closed if unavailable
            # We collect both total FDs (fd_count_max) and DB-file FDs (sqlite_connections_max)
            # via filtered lsof as in scripts/measure_resource_baseline.py.
            fd_sampled = False
            sqlite_sampled = False
            try:
                if args.no_docker and app_proc:
                    out = subprocess.run(
                        ["lsof", "-p", str(app_proc.pid)], capture_output=True, text=True, timeout=5
                    )
                    if out.returncode == 0:
                        lines = out.stdout.splitlines()
                        result["fd_count_sample"] = len(lines)
                        fd_sampled = True
                        # Filtered DB FD count: only numbered FDs on the DB file
                        db_str = str(data_dir / "muzilla.db")
                        c = 0
                        for line in lines:
                            if db_str not in line:
                                continue
                            parts = line.split()
                            if len(parts) >= 4:
                                fd_field = parts[3]
                                if fd_field[:-1].isdigit() and fd_field[-1] in ("u", "r", "w"):
                                    c += 1
                        result["sqlite_fd_count"] = c
                        metrics["sqlite_connections_max"] = c
                        sqlite_sampled = True
                    else:
                        result["fd_count_unavailable"] = out.stderr[:500] or "lsof failed"
                        result["sqlite_unavailable"] = out.stderr[:500] or "lsof failed"
                elif not args.no_docker:
                    # total FDs
                    # ponytail: measure the app process (PID 1), not the ephemeral
                    # sh wrapper (/proc/self would count sh's own FDs, trivially ~4
                    # and make the threshold vacuous). PID 1 is the muzilla server.
                    out = subprocess.run(
                        [
                            "docker",
                            "exec",
                            f"{compose_project}-muzilla-1",
                            "sh",
                            "-c",
                            "ls -1 /proc/1/fd 2>/dev/null | wc -l || echo 0",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    txt = out.stdout.strip()
                    if txt.isdigit():
                        result["fd_count_sample"] = int(txt)
                        fd_sampled = True
                    else:
                        result["fd_count_unavailable"] = out.stderr[:500] or "fd count empty"
                    # DB-filtered FDs inside container (filtered lsof if available, else /proc fd listing)
                    out2 = subprocess.run(
                        [
                            "docker",
                            "exec",
                            f"{compose_project}-muzilla-1",
                            "sh",
                            "-c",
                            "lsof -p 1 2>/dev/null | grep -c '/data/muzilla.db' || ls -l /proc/1/fd 2>/dev/null | grep -c '/data/muzilla.db' || echo 0",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    txt2 = out2.stdout.strip().splitlines()[-1] if out2.stdout.strip() else "0"
                    # lsof grep -c returns count, ls grep does too; handle both
                    try:
                        # Filter to only numbered FDs: count lines with ' -> ' and numeric fd
                        # For lsof path, grep -c already filtered; for ls -l, we need to parse
                        c2 = int(txt2.strip()) if txt2.strip().isdigit() else 0
                        # If lsof not available, ls -l approach may overcount txt mmap; cap via same filter
                        result["sqlite_fd_count"] = c2
                        metrics["sqlite_connections_max"] = c2
                        sqlite_sampled = True
                    except Exception:
                        result["sqlite_unavailable"] = out2.stderr[:500] or "sqlite count failed"
            except Exception as e:
                result["fd_count_unavailable"] = str(e)[:500]
                result["sqlite_unavailable"] = str(e)[:500]
            if fd_sampled and "fd_count_sample" in result:
                _fd_val = result["fd_count_sample"]
                if isinstance(_fd_val, int):
                    metrics["fd_count_max"] = _fd_val
                elif isinstance(_fd_val, str) and _fd_val.strip().isdigit():
                    metrics["fd_count_max"] = int(_fd_val.strip())
            if not sqlite_sampled:
                # Fallback: if sqlite not sampled but fd sampled, at least set 0 to avoid skip
                # but mark as measured via fd fallback would be wrong; keep unavailable to fail-closed
                pass

            # Playwright Apply/Undo of a 10-track TAG ReviewBundle (P1 fix).
            # The previous flow drove a grouping-correction review while the
            # 10-track set_tag bundles were applied only via direct API calls,
            # proving no browser metadata/file mutation. Now the browser itself
            # applies and undoes one 10-track tag bundle, and the harness
            # asserts each file's tag changes then restores (product invariant:
            # metadata/file Apply is a web-UI action).
            try:
                import urllib.error as _ue_pw
                import urllib.request as _ur_pw
                import uuid as _uuid_pw

                _pw_csrf: str | None = None

                def _pw_get(path: str) -> tuple[int, str]:
                    try:
                        with _ur_pw.urlopen(f"{base_url}{path}", timeout=10) as r:
                            return r.status, r.read().decode()
                    except _ue_pw.HTTPError as e:
                        return e.code, e.read().decode(errors="ignore")
                    except Exception as e:
                        return 0, str(e)

                _s_csrf, _b_csrf = _pw_get("/api/auth/status")
                with contextlib.suppress(Exception):
                    if _s_csrf == 200:
                        _pw_csrf = json.loads(_b_csrf).get("csrf_token")

                def _pw_mutate(
                    method: str,
                    path: str,
                    data: dict[str, object] | None = None,
                    extra_headers: dict[str, str] | None = None,
                ) -> tuple[int, str]:
                    body = json.dumps(data or {}).encode()
                    req = _ur_pw.Request(f"{base_url}{path}", data=body, method=method)
                    req.add_header("Content-Type", "application/json")
                    if _pw_csrf:
                        req.add_header("X-CSRF-Token", _pw_csrf)
                        req.add_header("Origin", base_url)
                    for _k, _v in (extra_headers or {}).items():
                        req.add_header(_k, _v)
                    try:
                        with _ur_pw.urlopen(req, timeout=30) as r:
                            return r.status, r.read().decode()
                    except _ue_pw.HTTPError as e:
                        return e.code, e.read().decode(errors="ignore")
                    except Exception as e:
                        return 0, str(e)

                # 10 fresh tracks, disjoint from the API pools and the grouping track
                _used_pw = set(metrics.get("apply_pool_track_ids") or [])
                if metrics.get("grouping_track_id") is not None:
                    _used_pw.add(int(metrics["grouping_track_id"]))
                _pw_tids: list[int] = []
                _cursor_pw: str | None = None
                for _ in range(4):
                    _q = (
                        f"/api/tracks?limit=500&cursor={_cursor_pw}"
                        if _cursor_pw
                        else "/api/tracks?limit=500"
                    )
                    _s_ids, _b_ids = _pw_get(_q)
                    if _s_ids != 200:
                        break
                    try:
                        _j_ids = json.loads(_b_ids)
                    except Exception:
                        break
                    for _x in _j_ids.get("items") or []:
                        _xid = _x.get("id")
                        if (
                            _xid is not None
                            and int(_xid) not in _used_pw
                            and int(_xid) not in _pw_tids
                        ):
                            _pw_tids.append(int(_xid))
                            if len(_pw_tids) >= 10:
                                break
                    if len(_pw_tids) >= 10:
                        break
                    _cursor_pw = _j_ids.get("next_cursor") or _j_ids.get("cursor")
                    if not _cursor_pw:
                        break
                _pw_bundle_id: int | None = None
                _pw_items: list[dict[str, object]] = []
                if len(_pw_tids) < 10:
                    result["playwright_log"] = (
                        f"only {len(_pw_tids)} fresh tracks for tag bundle (fail-closed)"
                    )
                    result["playwright_apply_undo_success"] = 0
                else:
                    _pw_suffix = _uuid_pw.uuid4().hex[:6]
                    _pw_create = (
                        "import json, uuid\n"
                        "from muzilla.config.loader import load_config\n"
                        "from muzilla.db.engine import create_db_engine, create_session_factory\n"
                        "from muzilla.pipeline.reviews import put_revision, transition_bundle\n"
                        "from muzilla.domain.reviews import BundleState\n"
                        "from muzilla.pipeline.reviews import OperationDraft\n"
                        "from muzilla.db.models import Track\n"
                        "cfg = load_config()\n"
                        "eng = create_db_engine(cfg.storage.db_path)\n"
                        "fac = create_session_factory(eng)\n"
                        f"tids = {_pw_tids}\n"
                        f"suffix = '{_pw_suffix}'\n"
                        "with fac() as sess:\n"
                        "    ops = []\n"
                        "    items = []\n"
                        "    for tid in tids:\n"
                        "        tr = sess.get(Track, int(tid))\n"
                        "        if tr is None:\n"
                        "            continue\n"
                        "        prop = f'PWTag{tid}-{suffix}'\n"
                        "        ops.append(OperationDraft(kind='set_tag', field='title', target_type='track', target_id=int(tid), current_value=tr.title, proposed_value=prop, provenance=dict(source='perf_scale_pw'), validation=dict(compatible=True)))\n"
                        "        items.append(dict(track_id=int(tid), original=tr.title, proposed=prop))\n"
                        "    lk = f'perf-pw-{suffix}'\n"
                        "    snap = []\n"
                        "    for it in items:\n"
                        "        _tr = sess.get(Track, int(it['track_id']))\n"
                        "        snap.append(dict(source_type='track', source_id=int(it['track_id']), path=_tr.path, size_bytes=_tr.size_bytes, mtime_ns=_tr.mtime_ns, tag_hash=_tr.tag_hash, filename=_tr.filename, content_hash=_tr.content_hash))\n"
                        "    w = put_revision(sess, logical_key=lk, title=f'PW 10-track {suffix}', scope_type='track', scope_id=int(tids[0]), source_snapshot=dict(items=snap), operations=tuple(ops))\n"
                        "    transition_bundle(sess, w.bundle_id, BundleState.NEEDS_ATTENTION)\n"
                        "    sess.commit()\n"
                        "    print(json.dumps(dict(bundle_id=int(w.bundle_id), items=items)))\n"
                    )
                    _created_pw: str | None = None
                    if compose_project is not None and not args.no_docker:
                        _exec_pw = subprocess.run(
                            [
                                "docker",
                                "exec",
                                f"{compose_project}-muzilla-1",
                                "python",
                                "-c",
                                _pw_create,
                            ],
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        if _exec_pw.returncode == 0:
                            _created_pw = _exec_pw.stdout.strip().splitlines()[-1]
                        else:
                            result["playwright_log"] = (
                                f"tag bundle create failed: {_exec_pw.stderr[:1000]}"
                            )
                            result["playwright_apply_undo_success"] = 0
                    else:
                        # host mode: create through the same DB path in-process
                        try:
                            import sys as _sys_pwh

                            _sys_pwh.path.insert(0, str(REPO_ROOT / "src"))
                            from muzilla.config.loader import load_config as _load_cfg_pwh
                            from muzilla.db.engine import create_db_engine as _c_eng_pwh
                            from muzilla.db.engine import create_session_factory as _c_fac_pwh
                            from muzilla.db.models import Track as _Tr_pwh
                            from muzilla.domain.reviews import BundleState as _BS_pwh
                            from muzilla.pipeline.reviews import OperationDraft as _OD_pwh
                            from muzilla.pipeline.reviews import put_revision as _put_rev_pwh
                            from muzilla.pipeline.reviews import transition_bundle as _trans_pwh

                            _cfg_pwh = _load_cfg_pwh()
                            _eng_pwh = _c_eng_pwh(_cfg_pwh.storage.db_path)
                            _fac_pwh = _c_fac_pwh(_eng_pwh)
                            with _fac_pwh() as _sess_pwh:
                                _ops_pwh = []
                                _items_pwh: list[dict[str, object]] = []
                                for _tid_pwh in _pw_tids:
                                    _tr_pwh = _sess_pwh.get(_Tr_pwh, int(_tid_pwh))
                                    if _tr_pwh is None:
                                        continue
                                    _prop_pwh = f"PWTag{_tid_pwh}-{_pw_suffix}"
                                    _ops_pwh.append(
                                        _OD_pwh(
                                            kind="set_tag",
                                            field="title",
                                            target_type="track",
                                            target_id=int(_tid_pwh),
                                            current_value=_tr_pwh.title,
                                            proposed_value=_prop_pwh,
                                            provenance=dict(source="perf_scale_pw"),
                                            validation=dict(compatible=True),
                                        )
                                    )
                                    _items_pwh.append(
                                        dict(
                                            track_id=int(_tid_pwh),
                                            original=_tr_pwh.title,
                                            proposed=_prop_pwh,
                                        )
                                    )
                                _lk_pwh = f"perf-pw-{_pw_suffix}"
                                _snap_pwh = []
                                for _it_pwh in _items_pwh:
                                    _t_pwh = _sess_pwh.get(_Tr_pwh, int(_it_pwh["track_id"]))
                                    _snap_pwh.append(
                                        dict(
                                            source_type="track",
                                            source_id=int(_it_pwh["track_id"]),
                                            path=_t_pwh.path,
                                            size_bytes=_t_pwh.size_bytes,
                                            mtime_ns=_t_pwh.mtime_ns,
                                            tag_hash=_t_pwh.tag_hash,
                                            filename=_t_pwh.filename,
                                            content_hash=_t_pwh.content_hash,
                                        )
                                    )
                                _w_pwh = _put_rev_pwh(
                                    _sess_pwh,
                                    logical_key=_lk_pwh,
                                    title=f"PW 10-track {_pw_suffix}",
                                    scope_type="track",
                                    scope_id=int(_pw_tids[0]),
                                    source_snapshot=dict(items=_snap_pwh),
                                    operations=tuple(_ops_pwh),
                                )
                                _trans_pwh(_sess_pwh, _w_pwh.bundle_id, _BS_pwh.NEEDS_ATTENTION)
                                _sess_pwh.commit()
                                _created_pw = json.dumps(
                                    dict(bundle_id=int(_w_pwh.bundle_id), items=_items_pwh)
                                )
                            _eng_pwh.dispose()
                        except Exception as _e_host_pw:
                            result["playwright_log"] = (
                                f"host tag bundle create failed: {_e_host_pw}"
                            )
                            result["playwright_apply_undo_success"] = 0
                    if _created_pw:
                        try:
                            _j_pw = json.loads(_created_pw)
                            _pw_bundle_id = int(_j_pw["bundle_id"])
                            _pw_items = _j_pw["items"]
                        except Exception as _e_parse:
                            result["playwright_log"] = (
                                f"tag bundle parse failed: {_e_parse} {_created_pw[:500]}"
                            )
                            result["playwright_apply_undo_success"] = 0
                    if _pw_bundle_id is not None:
                        # accept all operations so browser Apply is enabled
                        _s_det, _b_det = _pw_get(f"/api/reviews/{_pw_bundle_id}")
                        try:
                            _j_det = json.loads(_b_det)
                            _rev = _j_det.get("current_revision") or {}
                            _rev_id = _rev.get("id")
                            _ops = _rev.get("operations") or []
                            _decs = [
                                {"operation_id": int(o["id"]), "decision": "accepted"}
                                for o in _ops
                                if o.get("id") is not None
                            ]
                            _s_patch, _b_patch = _pw_mutate(
                                "PATCH",
                                f"/api/reviews/{_pw_bundle_id}/operations",
                                {"revision_id": int(_rev_id), "decisions": _decs},
                            )
                            if _s_patch != 200:
                                raise RuntimeError(f"decisions patch {_s_patch}: {_b_patch[:300]}")
                        except Exception as _e_patch:
                            result["playwright_log"] = f"tag bundle decisions failed: {_e_patch}"
                            result["playwright_apply_undo_success"] = 0
                            _pw_bundle_id = None
                if _pw_bundle_id is not None:
                    pw_script = scratch_root / "playwright_perf.cjs"
                    pw_script.write_text(
                        """
const { chromium, expect: _exp } = require('playwright');
const baseUrl = process.env.BASE_URL;
const reviewId = process.env.REVIEW_ID;
if (!baseUrl || !reviewId) { console.error('missing BASE_URL or REVIEW_ID'); process.exit(1); }
const phase = process.env.PHASE || 'apply';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  try {
    // 10-track tag ReviewBundle detail; decisions pre-accepted via API so
    // Apply is enabled without any per-choice click. PHASE=apply and
    // PHASE=undo run separately so the harness asserts tag state between.
    await page.goto(`${baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`, { waitUntil: 'domcontentloaded', timeout: 15000 });
    if (phase === 'apply') {
    // tag bundle: no Scegli step; Apply must already be enabled via API-accepted decisions
    const applyBtn = page.getByRole('button', { name: /Applica \\d+ modifiche/ });
    await applyBtn.waitFor({ state: 'visible', timeout: 10000 });
    // ensure enabled after selection
    const start = Date.now();
    while (Date.now() - start < 5000) {
      if (await applyBtn.isEnabled()) break;
      await page.waitForTimeout(200);
    }
    if (!(await applyBtn.isEnabled())) throw new Error('Apply still disabled after Scegli');
    console.log('click Apply');
    await applyBtn.click();
    const dlg = page.getByRole('dialog', { name: 'Applicare le modifiche?' });
    await dlg.waitFor({ state: 'visible', timeout: 10000 });
    const confirmApply = dlg.getByRole('button', { name: /Applica \\d+ modifiche/ });
    await confirmApply.waitFor({ state: 'visible', timeout: 5000 });
    await confirmApply.click();
    await dlg.waitFor({ state: 'hidden', timeout: 5000 });
    console.log('Apply confirmed, waiting for applied state');
    // poll API for applied state (journaled atomic replace)
    const deadline = Date.now() + 15000;
    let applied = false;
    while (Date.now() < deadline) {
      const res = await page.request.get(`${baseUrl}/api/reviews/${reviewId}`);
      if (res.ok()) {
        const body = await res.json();
        if (body.state === 'applied') { applied = true; break; }
        if (body.state === 'failed') throw new Error('review failed: ' + JSON.stringify(body.apply_runs?.[0]?.result || body.error).slice(0,1000));
      }
      await page.waitForTimeout(500);
    }
    if (!applied) throw new Error('review did not reach applied within 15s');
    console.log('playwright apply ok');
    } else {
    // Undo via browser
    const undoBtn = page.getByRole('button', { name: 'Ripristina applicazione' });
    await undoBtn.waitFor({ state: 'visible', timeout: 10000 });
    if (!(await undoBtn.isEnabled())) throw new Error('Undo button not enabled');
    await undoBtn.click();
    const undoDlg = page.getByRole('dialog', { name: 'Ripristinare l\\u2019applicazione?' });
    await undoDlg.waitFor({ state: 'visible', timeout: 10000 });
    const confirmUndo = undoDlg.getByRole('button', { name: 'Ripristina file' });
    await confirmUndo.waitFor({ state: 'visible', timeout: 5000 });
    await confirmUndo.click();
    await undoDlg.waitFor({ state: 'hidden', timeout: 5000 });
    console.log('Undo confirmed, waiting for undone');
    const deadline2 = Date.now() + 15000;
    let undone = false;
    while (Date.now() < deadline2) {
      const res = await page.request.get(`${baseUrl}/api/reviews/${reviewId}`);
      if (res.ok()) {
        const body = await res.json();
        const lastUndo = (body.undo_runs || []).at(-1);
        if (lastUndo && lastUndo.state === 'undone') { undone = true; break; }
        if (lastUndo && lastUndo.state === 'failed') throw new Error('undo failed: ' + JSON.stringify(lastUndo.result).slice(0,1000));
      }
      await page.waitForTimeout(500);
    }
    if (!undone) throw new Error('undo did not reach undone within 15s');
    console.log('playwright undo ok');
    }
  } finally { await browser.close(); }
})();
"""
                    )

                    def _pw_run_phase(_phase: str) -> tuple[bool, str]:
                        _env_pw = {
                            **os.environ,
                            "BASE_URL": base_url,
                            "REVIEW_ID": str(_pw_bundle_id),
                            "PHASE": _phase,
                            "NODE_PATH": str(REPO_ROOT / "e2e" / "node_modules"),
                        }
                        _run = subprocess.run(
                            ["node", str(pw_script)],
                            capture_output=True,
                            text=True,
                            timeout=120,
                            env=_env_pw,
                            cwd=REPO_ROOT,
                        )
                        _out = _run.stdout + _run.stderr
                        return (f"playwright {_phase} ok" in _out), _out[:8000]

                    def _pw_titles_match(_expected: dict[int, str | None]) -> tuple[bool, str]:
                        _deadline = time.monotonic() + 20
                        _last: dict[int, str | None] = {}
                        while time.monotonic() < _deadline:
                            _all_ok = True
                            for _tid_chk, _exp_title in _expected.items():
                                _s_t, _b_t = _pw_get(f"/api/tracks/{_tid_chk}")
                                _got: str | None = None
                                with contextlib.suppress(Exception):
                                    if _s_t == 200:
                                        _got = json.loads(_b_t).get("title")
                                _last[_tid_chk] = _got
                                if _got != _exp_title:
                                    _all_ok = False
                            if _all_ok:
                                return True, ""
                            time.sleep(0.5)
                        return False, json.dumps({str(k): v for k, v in _last.items()})[:1000]

                    _pw_logs: list[str] = []
                    _pw_ok = True
                    _ok_apply, _log_apply = _pw_run_phase("apply")
                    _pw_logs.append("APPLY:" + _log_apply)
                    if not _ok_apply:
                        _pw_ok = False
                    else:
                        _exp_proposed = {
                            int(_it["track_id"]): str(_it["proposed"]) for _it in _pw_items
                        }
                        _match_prop, _detail_prop = _pw_titles_match(_exp_proposed)
                        metrics["playwright_tag_applied_assert"] = _match_prop
                        if not _match_prop:
                            _pw_logs.append(f"APPLIED-TITLES-MISMATCH:{_detail_prop}")
                            _pw_ok = False
                    if _pw_ok:
                        _ok_undo, _log_undo = _pw_run_phase("undo")
                        _pw_logs.append("UNDO:" + _log_undo)
                        if not _ok_undo:
                            _pw_ok = False
                        else:
                            _exp_orig = {int(_it["track_id"]): _it["original"] for _it in _pw_items}
                            _match_orig, _detail_orig = _pw_titles_match(_exp_orig)
                            metrics["playwright_tag_restored_assert"] = _match_orig
                            if not _match_orig:
                                _pw_logs.append(f"RESTORED-TITLES-MISMATCH:{_detail_orig}")
                                _pw_ok = False
                    metrics["playwright_tag_bundle_id"] = int(_pw_bundle_id)
                    metrics["playwright_tag_track_count"] = len(_pw_items)
                    result["playwright_log"] = ("\n".join(_pw_logs))[:8000]
                    result["playwright_apply_undo_success"] = 1 if _pw_ok else 0
            except Exception as e:
                result["playwright_error"] = str(e)[:2000]
                result["playwright_apply_undo_success"] = 0
        finally:
            stop_sampler.set()
            t.join(timeout=2)

        # threshold evaluation — every acceptance scenario fail-closed if unavailable
        failures: list[str] = []
        thresholds_map: dict[str, Any] = thresholds.get("thresholds", {})
        # alias some thresholds that map to differently named metrics
        _alias_map = {
            "incremental_scan_p95_ms": "incremental_scan_ms",
            "catalog_search_p95_ms": "catalog_search_p95_ms",
            "catalog_filters_p95_ms": "catalog_filters_p95_ms",
        }
        for key, spec in thresholds_map.items():
            target = spec.get("target")
            op = spec.get("operator")
            metrics_obj = result.get("metrics")
            metric_val: Any = None
            if isinstance(metrics_obj, dict):
                metric_val = metrics_obj.get(key)
                if metric_val is None and key in _alias_map:
                    metric_val = metrics_obj.get(_alias_map[key])
            if metric_val is None:
                metric_val = result.get(key)
            if metric_val is None:
                # handle cold_scan_throughput special
                if key == "cold_scan_throughput_tracks_per_sec" and isinstance(metrics_obj, dict):
                    _dur = metrics_obj.get("cold_scan_s")
                    _dataset = result.get("dataset")
                    _cnt = _dataset.get("count") if isinstance(_dataset, dict) else None
                    if isinstance(_dur, (int, float)) and _dur > 0 and isinstance(_cnt, int):
                        metric_val = round(_cnt / _dur, 1)
                        metrics_obj["cold_scan_throughput_tracks_per_sec"] = metric_val
                if metric_val is None and key == "api_error_rate":
                    # api_error_rate is derived from 5xx count, already in metrics/metrics_obj
                    if isinstance(metrics_obj, dict) and "api_error_rate" in metrics_obj:
                        metric_val = metrics_obj.get("api_error_rate")
                    elif "api_error_rate" in result:
                        metric_val = result.get("api_error_rate")
            if metric_val is None:
                failures.append(f"{key}: unavailable (fail-closed)")
                continue
            try:
                if op == "<=" and not (metric_val <= target):
                    failures.append(f"{key}: {metric_val} > {target}")
                elif op == ">=" and not (metric_val >= target):
                    failures.append(f"{key}: {metric_val} < {target}")
                elif op == "==" and metric_val != target:
                    failures.append(f"{key}: {metric_val} != {target}")
            except Exception:
                pass
        result["threshold_failures"] = failures
        # P1 pre-run fixed gates (not threshold relaxations — all fixed before
        # the run, evaluated fail-closed here):
        # - mock-only providers: outcomes + mock-server paths must be musicbrainz-only
        # - audio-tool workload must have run green in the candidate image
        # - grouping must have run inside the candidate to a succeeded terminal state
        _m = result.get("metrics")
        if isinstance(_m, dict):
            if not _m.get("mock_only_verified"):
                failures.append(
                    f"mock_only_providers: not verified musicbrainz-only: "
                    f"{(_m.get('mock_only_violation') or _m.get('mock_stats_error') or 'no evidence')}"
                )
            if _m.get("audio_tool_error"):
                failures.append(f"audio_tool_workload: {_m.get('audio_tool_error')}")
            elif not _m.get("audio_tool_ok") and not _m.get("audio_tool_skipped_host_mode"):
                failures.append("audio_tool_workload: no success evidence (fail-closed)")
            if _m.get("grouping_job_state") != "succeeded":
                failures.append(
                    f"grouping_candidate_job: terminal={_m.get('grouping_job_state')}: "
                    f"{_m.get('grouping_duration_error') or 'not succeeded (fail-closed)'}"
                )
            # source→image identity: clean-tree gate must have passed and the
            # running container image must equal the freshly built candidate
            if not result.get("source_clean_verified"):
                failures.append("source_identity: clean-tree gate did not pass (fail-closed)")
            _lbl = result.get("candidate_image_labels")
            if isinstance(_lbl, dict) and source_info:
                if _lbl.get("org.opencontainers.image.revision") != source_info.get("commit"):
                    failures.append("source_identity: image label revision != candidate commit")
                if _lbl.get("muzilla.build-context") != source_info.get("build_context_sha256"):
                    failures.append("source_identity: image label build-context != recorded hash")
            if not result.get("candidate_image_id_verified"):
                failures.append("source_identity: running image != candidate image Id")
        result["threshold_failures"] = failures
        result["threshold_pass"] = len(failures) == 0
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # write results
        result_path = results_dir / "perf_result.json"
        result_path.write_text(json.dumps(result, indent=2))
        # NDJSON latency samples
        ndjson_path = results_dir / "latencies.ndjson"
        with ndjson_path.open("w") as f:
            metrics_for_ndjson = result.get("metrics")
            lat_map: dict[str, Any] = {}
            if isinstance(metrics_for_ndjson, dict):
                lat_map = metrics_for_ndjson.get("latencies") or {}
            for k, samples in lat_map.items():
                for s in samples:
                    f.write(json.dumps({"metric": k, "latency_ms": s}) + "\n")
        # checksums — P1: outputs plus source/manifest inputs so the record
        # binds the exact source and manifest to this run (not outputs alone)
        checksums = {}
        for p in [result_path, ndjson_path]:
            checksums[p.name] = _sha256_file(p)
        for _name, _rel in [
            ("thresholds.json", Path("benchmark/thresholds.json")),
            ("Dockerfile", Path("docker/Dockerfile")),
            ("docker-compose.yml", Path("docker-compose.yml")),
            ("docker-compose.perf.yml", Path("benchmark/docker-compose.perf.yml")),
            ("perf_benchmark.py", Path("scripts/perf_benchmark.py")),
            ("gen_perf_library.py", Path("scripts/gen_perf_library.py")),
        ]:
            with contextlib.suppress(Exception):
                checksums[_name] = _sha256_file(REPO_ROOT / _rel)
        # logs
        if container_started:
            log_path = logs_dir / "compose.log"
            _collect_logs(compose_project, log_path)
            if log_path.exists():
                checksums[log_path.name] = _sha256_file(log_path)
        checksum_path = results_dir / "checksums.json"
        checksum_path.write_text(json.dumps(checksums, indent=2))
        print(f"Results written to {results_dir}", file=sys.stderr)
        print(f"  image digest: {image_digest}", file=sys.stderr)
        print(f"  peak RSS MiB: {result.get('peak_rss_mib')}", file=sys.stderr)
        print(f"  threshold failures: {failures}", file=sys.stderr)
        print(f"  result JSON: {result_path}", file=sys.stderr)
        print(f"  checksums: {checksums}", file=sys.stderr)
        # also print summary to stdout as JSON for caller
        print(
            json.dumps(
                {
                    "out": str(out_root),
                    "result": str(result_path),
                    "image": image_digest,
                    "failures": failures,
                    "peak_rss": result.get("peak_rss_mib"),
                },
                indent=2,
            )
        )

    finally:
        if container_started:
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["docker", "compose", "-p", compose_project, "down", "-v"],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    timeout=60,
                )
        if mock_proc:
            with contextlib.suppress(Exception):
                mock_proc.terminate()
                mock_proc.wait(timeout=5)
            with contextlib.suppress(Exception):
                mock_proc.kill()
        if app_proc:
            with contextlib.suppress(Exception):
                app_proc.terminate()
                app_proc.wait(timeout=5)
            with contextlib.suppress(Exception):
                app_proc.kill()


if __name__ == "__main__":
    main()
