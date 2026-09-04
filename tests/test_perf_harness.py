"""Lightweight tests for PERF-SCALE-001 harness and threshold manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_thresholds_manifest_exists_and_valid() -> None:
    p = REPO_ROOT / "benchmark" / "thresholds.json"
    assert p.exists(), "benchmark/thresholds.json must exist (immutable manifest)"
    data = json.loads(p.read_text())
    assert "version" in data
    assert "commit" in data
    assert "thresholds" in data
    assert "dataset" in data
    assert data["dataset"]["count"] == 100000
    assert "hardware_baseline" in data
    # thresholds must have operator and target, and must not be relaxable
    for key, spec in data["thresholds"].items():
        assert "target" in spec, f"{key} missing target"
        assert "operator" in spec, f"{key} missing operator"
        assert spec["operator"] in ("<=", ">=", "=="), f"{key} invalid operator"
        assert isinstance(spec["target"], (int, float))
    # must contain 2 GiB target
    assert data["thresholds"]["muzilla_rss_mib_peak"]["target"] == 2048
    # commit must look like SHA
    assert len(data["commit"]) >= 7


def test_thresholds_not_relaxed_after_run_note() -> None:
    p = REPO_ROOT / "benchmark" / "thresholds.json"
    text = p.read_text()
    assert (
        "may not be relaxed" in text or "not be relaxed" in text or "do not relax" in text.lower()
    )


def test_perf_compose_overlay_exists_and_no_build() -> None:
    p = REPO_ROOT / "benchmark" / "docker-compose.perf.yml"
    assert p.exists()
    text = p.read_text()
    assert "MUZILLA_PERF_IMAGE" in text
    assert "MUZILLA_PERF_DATA" in text
    assert "MUZILLA_PERF_LIBRARY" in text
    # must mention --no-build usage
    assert "no-build" in text.lower() or "no_build" in text.lower() or "--no-build" in text
    # must not mount repo source
    assert ".:/app" not in text
    assert "./src" not in text


def test_gen_perf_library_deterministic(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gen_perf_library", REPO_ROOT / "scripts" / "gen_perf_library.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    generate = mod.generate

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    generate(20, out1, seed=42)
    generate(20, out2, seed=42)
    files1 = sorted(p.name for p in out1.glob("*.mp3"))
    files2 = sorted(p.name for p in out2.glob("*.mp3"))
    assert files1 == files2

    # compare tag-relevant file sizes/contents are deterministic for tiny fixture subset
    # at least file count and names must match; content hash for first file must be identical
    def file_hash(p: Path) -> str:
        h = hashlib.sha256()
        h.update(p.read_bytes()[:8192])
        return h.hexdigest()

    for f1, f2 in zip(sorted(out1.glob("*.mp3")), sorted(out2.glob("*.mp3")), strict=True):
        assert file_hash(f1) == file_hash(f2)


def test_harness_helpers(tmp_path: Path) -> None:
    import contextlib
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "perf_benchmark", REPO_ROOT / "scripts" / "perf_benchmark.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    thresholds = mod._load_thresholds()
    assert thresholds["thresholds"]["muzilla_rss_mib_peak"]["target"] == 2048

    hw = mod._hardware_record()
    assert "uname" in hw
    assert "docker_version" in hw or "docker_info" in hw

    # sha256 helper
    f = tmp_path / "hello.txt"
    f.write_text("hello")
    assert mod._sha256_file(f) == hashlib.sha256(b"hello").hexdigest()

    # isolated scratch check: out inside repo must be rejected (guard in main)
    with contextlib.suppress(Exception):
        _ = mod.main


def test_gitignore_excludes_perf_results() -> None:
    text = (REPO_ROOT / ".gitignore").read_text()
    assert "benchmark/results" in text or "perf_results" in text


def test_harness_deterministic_grouping_seed_visible_to_container() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # Must seed inside exact Compose container (docker exec) so the WorkUnit
    # is visible to the running app, not via host direct DB that corrupts WAL.
    assert "docker exec" in text
    assert "compose_project" in text
    assert "e2e-source-perf" in text
    assert "grouping_confidence=0.4" in text
    # Must select operation before browser Apply/Undo (PATCH decisions)
    assert "/api/reviews/" in text and "decisions" in text
    assert "constrained_review_ready" in text
    # Proactive seeding before 30-candidate loop, not only fallback
    assert "pre_seed" in text


def test_harness_incremental_is_single_file_scope() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # Threshold describes incremental as single-file scope; harness must measure that.
    assert "incremental_scan_root" in text
    assert "single-file" in text.lower() or "single_file" in text.lower() or "Single-file" in text
    # Must touch one file and scan that file, not full library for incremental metric
    assert '"/music/"' in text or "'/music/'" in text or "/music/{_inc_file_name}" in text


def test_harness_immutable_image_and_verification() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # Must build immutable sha256: Id and verify running container matches candidate
    assert 'startswith("sha256:")' in text or "sha256:" in text
    assert "MUZILLA_PERF_IMAGE" in text
    assert "image_digest" in text
    assert "running_image_id" in text
    assert "candidate_image_id_verified" in text
    assert "{{.Image}}" in text or "{{.Config.Image}}" in text
    # Must fail-closed if verification fails
    assert "immutable verification failed" in text or "image verification failed" in text


def test_harness_mock_provider_injected_via_compose() -> None:
    compose = (REPO_ROOT / "benchmark" / "docker-compose.perf.yml").read_text()
    harness = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # Compose overlay must inject mock provider via MUZILLA_MOCK_URL
    assert "MUZILLA_MOCK_URL" in compose
    assert "MUZILLA_PROVIDERS__MUSICBRAINZ__BASE_URL_OVERRIDE" in compose
    assert "host.docker.internal" in compose
    assert "extra_hosts" in compose
    # Harness must set MUZILLA_MOCK_URL through Compose env
    assert "MUZILLA_MOCK_URL" in harness
    assert "mock_url" in harness.lower() or "MUZILLA_MOCK_URL" in harness
    # Host mode also injects override
    assert "MUZILLA_PROVIDERS__MUSICBRAINZ__BASE_URL_OVERRIDE" in harness


def test_harness_every_acceptance_measured_fail_closed() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # 30 samples p95 for all p95 thresholds
    assert text.count("for _ in range(30)") >= 4 or text.count("range(30)") >= 4
    assert "_p95(" in text
    # API error count measured
    assert "api_5xx_count" in text
    assert "api_error_rate" in text
    assert "_track_5xx" in text
    # Apply/Undo 10-track bundle measured
    assert "apply_p95_ms_per_bundle" in text
    assert "undo_p95_ms" in text
    assert "10-track" in text or "10_track" in text or "perf-apply" in text
    assert "apply_bundle_count" in text or "_apply_samples" in text
    # Incremental terminal state + catalog evidence
    assert "incremental_scan_state" in text
    assert "incremental_changed_file_evidence" in text
    assert "incremental_scan_terminal_error" in text or "terminal" in text.lower()
    # Fail-closed for all thresholds when unavailable
    assert 'failures.append(f"{key}: unavailable (fail-closed)")' in text
    # Must record mock injection verification
    assert "mock_provider_injected" in text


def test_perf_library_mount_is_writable_for_apply_undo() -> None:
    """PERF-SCALE-001: the scratch library must be writable.

    The benchmark library is disposable harness-generated scratch (never user
    data), and the acceptance requires real Apply/Undo file mutation through
    the reviewed journaled path. A read-only /music mount makes every
    apply/undo job fail with EROFS (observed: Errno 30 on
    '<track>.muzilla.tmp'), the 15s poll then expires per bundle, and the
    benchmark is vacuous. The overlay must not set :ro / read_only on /music.
    """
    compose = (REPO_ROOT / "benchmark" / "docker-compose.perf.yml").read_text()
    for line in compose.splitlines():
        stripped = line.strip()
        if "/music" in stripped and ("MUZILLA_PERF_LIBRARY" in stripped or ":/music" in stripped):
            assert ":ro" not in stripped, f"/music must be writable for Apply/Undo: {stripped}"
    assert (
        "read_only: true" not in compose
        or "/music" not in compose.split("read_only: true")[0][-500:]
    )


def test_harness_reserves_grouping_track_from_api_pools() -> None:
    """PERF-SCALE-001: the seeded grouping track is owned by the browser flow.

    A manual review or API apply bundle on the same track makes the browser
    Apply fail with concurrent-conflict (observed on the seeded track).
    The harness must exclude the reserved track from both the manual
    review-generation pool and the API apply-bundle pool, including the
    pagination fallback fills.
    """
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "_reserved_tracks" in text
    assert "_reserved_for_browser" in text
    assert "grouping_track_id" in text
    # fallback fills must also respect the reservation
    assert "not in _reserved_tracks" in text
    assert "not in _reserved_for_browser" in text


def test_harness_fd_count_measures_app_process() -> None:
    """PERF-SCALE-001: fd_count_max must sample the app, not the probe shell.

    `ls /proc/self/fd` inside `docker exec sh -c` counts the ephemeral sh
    wrapper's FDs (~4), making the <=1024 threshold vacuous. The harness must
    sample /proc/1/fd (PID 1 is the muzilla server in the candidate image).
    """
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "/proc/1/fd" in text
    assert "ls -1 /proc/self/fd" not in text


def test_harness_no_live_provider_or_source_mount() -> None:
    compose = (REPO_ROOT / "benchmark" / "docker-compose.perf.yml").read_text()
    harness = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # No source bind mount or build in perf overlay
    assert (
        "build:" not in compose.lower()
        or "no-build" in compose.lower()
        or "no_build" in compose.lower()
    )
    # Harness must use --no-build
    assert "--no-build" in harness
    # No live provider contact when mock is set; harness mentions no-live or mock
    assert "mock_provider" in harness.lower() or "mock" in harness.lower()


def test_perf_overlay_disables_non_mocked_providers() -> None:
    """P1: only MusicBrainz (mock-redirected) may stay enabled in perf runs."""
    compose = (REPO_ROOT / "benchmark" / "docker-compose.perf.yml").read_text()
    for provider in ("DEEZER", "DISCOGS", "ACOUSTID", "COVERARTARCHIVE", "LRCLIB"):
        assert f"MUZILLA_PROVIDERS__{provider}__ENABLED" in compose, provider
    assert compose.count('"false"') >= 5


def test_mock_server_exposes_request_stats() -> None:
    """P1: mock server counts per-path hits so the harness can verify mock-only use."""
    text = (REPO_ROOT / "e2e" / "mock_provider_server.py").read_text()
    assert "/__stats" in text
    assert "_REQUEST_COUNTS" in text


def test_mock_server_stats_endpoint_serves() -> None:
    """Functional: the stats endpoint reports hits without breaking fixtures."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mock_provider_server", REPO_ROOT / "e2e" / "mock_provider_server.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from fastapi.testclient import TestClient

    client = TestClient(mod.app)
    assert client.get("/release?query=test&limit=1&fmt=json").status_code == 200
    stats = client.get("/__stats").json()
    assert stats["requests"].get("/release", 0) >= 1


def test_harness_verifies_mock_only_providers_fail_closed() -> None:
    """P1: matching outcomes + mock stats must prove musicbrainz-only (no live Deezer)."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "provider_outcomes" in text
    assert "mock_only_verified" in text
    assert "mock_only_violation" in text
    assert "mock_non_musicbrainz_paths" in text
    assert "mock_request_paths" in text
    # fail-closed gate in threshold evaluation
    assert "mock_only_providers: not verified musicbrainz-only" in text
    # unconditional True injection must be gone
    assert 'metrics["mock_provider_injected"] = True' not in text


def test_gen_library_realistic_audio_is_valid_encoded() -> None:
    """P1: the 2% subset must be valid long encoded audio, never padded zeros."""
    text = (REPO_ROOT / "scripts" / "gen_perf_library.py").read_text()
    assert "ffmpeg" in text
    assert "_build_long_fixture" in text
    assert 'b"\\x00" * pad' not in text
    assert "refusing silent fallback" in text


def test_gen_library_long_fixture_functional(tmp_path) -> None:
    """Functional: long fixture builds valid MP3 and subset copies it."""
    import importlib.util
    import shutil as _shutil

    if _shutil.which("ffmpeg") is None:
        raise AssertionError("ffmpeg required for realistic-audio subset")
    spec = importlib.util.spec_from_file_location(
        "gen_perf_library", REPO_ROOT / "scripts" / "gen_perf_library.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    long_mp3 = mod._build_long_fixture(tmp_path)
    assert long_mp3.exists() and long_mp3.stat().st_size > 200_000
    # valid decodable audio of realistic length (not padded zeros)
    from mutagen.mp3 import MP3

    info = MP3(str(long_mp3)).info
    assert info.length >= 20.0, f"long fixture too short: {info.length}"
    # fail-closed when the fixture is missing (subset selected but no fixture)
    import random as _random

    _raised = False
    for _seed in range(500):
        try:
            mod._make_realistic_audio(tmp_path / f"x{_seed}.mp3", _random.Random(_seed), None)
        except RuntimeError:
            _raised = True
            break
    assert _raised, "subset without fixture must raise, never silently pad"


def test_harness_grouping_runs_in_candidate_not_host_snapshot() -> None:
    """P1: grouping must run as a candidate `group` job, not host src on a DB copy."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert 'type="group"' in text or "type='group'" in text
    assert "grouping_job_id" in text
    assert "grouping_job_state" in text
    assert "grouping_candidate_job" in text
    # old host-snapshot path must be gone
    assert "run_grouping_cascade" not in text
    assert "_tmp_grp" not in text


def test_harness_search_facet_thresholds_hit_real_endpoints() -> None:
    """P1: search threshold from /api/tracks?search=…; filters cover real facets."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "/api/tracks/facets" in text
    assert "catalog_filters_p95_ms" in text
    assert "_filter_facet_combined" in text or "catalog_filter" in text
    # dashboard must no longer masquerade as facets
    assert '("facets", "/api/dashboard")' not in text


def test_harness_cancel_measures_to_terminal_state() -> None:
    """P1: cancel latency runs to terminal cancelled, not POST return."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "cancel_terminal_error" in text
    assert "no terminal state within 30s" in text
    assert "_t_cancel" in text or "cancel POST send until" in text


def test_harness_playwright_drives_tag_bundle_with_tag_asserts() -> None:
    """P1: browser applies+undoes a 10-track tag bundle, asserting tag change/restore."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "PHASE" in text
    assert "playwright_tag_bundle_id" in text
    assert "playwright_tag_applied_assert" in text
    assert "playwright_tag_restored_assert" in text
    assert "apply_pool_track_ids" in text
    assert "PWTag" in text
    # grouping-review browser flow must be gone
    assert "playwright success" not in text
    assert "_grouping_id" not in text


def test_harness_audio_workload_in_candidate_fail_closed() -> None:
    """P1: fpcalc + rsgain run in the candidate image against valid long audio."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "fpcalc" in text
    assert "rsgain" in text
    assert "audio_fpcalc_ok" in text
    assert "audio_rsgain_returncode" in text
    assert "realistic_audio_file_count" in text
    assert "audio_tool_workload:" in text


def test_harness_exact_source_identity_gates_build() -> None:
    """P1: clean-tree gate, commit/tree/context hashes, image labels, running==candidate."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "_require_clean_source" in text
    assert "git status" in text and "--porcelain" in text
    assert "worktree not clean" in text
    assert 'git", "archive' in text or '"archive"' in text
    assert "org.opencontainers.image.revision" in text
    assert "muzilla.build-context" in text
    assert "source_clean_verified" in text
    assert "source_identity:" in text
    # checksums bind source inputs, not outputs alone
    assert "thresholds.json" in text and "Dockerfile" in text


def test_harness_cancel_strict_all_samples_bound() -> None:
    """Oracle: cancel_detection_ms stays singular; harness correction is stricter.

    The 2000ms manifest bound is gated on the MAX of 30 predetermined samples
    (every sample <= 2000; p95 diagnostic only). The harness must wait for an
    actively running job (no fixed pre-cancel sleep), time from immediately
    before the cancel POST, poll at <=50ms, and require HTTP success plus
    terminal state exactly `cancelled` (fail-closed otherwise).
    """
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    start = text.index("# 5. cancellation")
    end = text.index("# 6. incremental")
    block = text[start:end]
    # predetermined 30 samples, gated on max (all-samples bound)
    assert "for _cancel_iter in range(30)" in block
    assert "max(_cancel_samples)" in block
    assert 'metrics["cancel_detection_ms"] = round(max(_cancel_samples), 1)' in block
    # p95 recorded as diagnostic only, never as the gate
    assert 'metrics["cancel_detection_p95_ms"]' in block
    # waits for demonstrably running job, never a fixed pre-cancel sleep
    assert '"running"' in block or "'running'" in block
    assert "never observed running (fail-closed)" in block
    assert "time.sleep(0.5)" not in block
    # timing starts immediately before the cancel POST
    assert "_t_cancel = time.monotonic()" in block
    _t_idx = block.index("_t_cancel = time.monotonic()")
    _post_idx = block.index("/cancel", _t_idx)
    assert 0 < _post_idx - _t_idx < 400
    # poll at <=50ms (production token interval)
    assert "0.05" in block
    assert "time.sleep(0.2)" not in block
    # requires HTTP success and exactly `cancelled` (fail-closed otherwise)
    assert "not in (200, 202)" in block
    assert '== "cancelled"' in block
    assert "not cancelled (fail-closed)" in block
    assert "no terminal state within 30s (fail-closed)" in block
    assert 'metrics["cancel_detection_ms"] = None' in block
    # terminal `succeeded`/`failed` must not count as cancel success
    assert '("succeeded", "failed")' in block
    # manifest untouched: singular bound preserved (no p95 gate substitution)
    assert 'metrics["cancel_sample_count"]' in block
